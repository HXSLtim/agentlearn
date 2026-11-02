"""同会话并发的裁判：steering 三档。

用户在同一个会话里连发消息时，不能让两轮 turn 交错写 JSONL。
三档的差别只有一个——新消息愿意等多久（设计定稿见 TODO 的 steering 节，
术语对照：agent 圈把「执行中途改方向」统称 steering；抢占/协作这对区分
来自操作系统调度，比 agent 圈的叫法更精确）：

    queue        顺延。等当前轮 end_turn，再按先来后到跑自己的轮。
                 等待单位：一整轮。
    cooperative  软中断。把消息塞进运行轮的 inbox 立刻返回；运行轮在下一个
                 圈界（厂商请求间隙）排干 inbox 并进历史。当前流与当前工具
                 跑完。等待单位：一次请求。
    preemptive   抢占。设取消标志；运行轮在下一个检查点（事件边界或圈界）
                 抛 TurnCancelled 退出，半截回答不落盘；然后新消息跑自己的
                 轮。等待单位：趋近于零——阻塞中的网络读打断不了，上限是
                 「下一个事件到达或下一圈开始」。

实现只管「谁在跑、要插入什么、要不要打断」这三样状态；怎么跑轮次仍是
Conversation 的事（它通过 steering 钩子读写这里，鸭子类型，不 import 本模块
也能工作——TUI 就不传）。线程模型：FastAPI 的同步端点跑在线程池里，
每个请求一个线程，所以这里全是 threading 的锁和事件，没有 asyncio。
"""
import threading

from application.approval import APPROVAL_MODES


# 三档的名字。请求里的 steering 字段必须是这三个之一。
MODES = ("queue", "cooperative", "preemptive")


class RunningTurn:
    """一次正在跑的轮次，可以被并发请求触到的运行时把手。

    cancel：抢占档设的取消标志，Conversation 的检查点轮询它。
    inbox：软中断塞进来的新消息，圈界处被排干。
    done：这轮结束（无论怎么结束）被置位，抢占者靠它等旧轮退场。
    approval_mode：执行等级。切换后立即影响这一轮后续的工具调用，
    等待中的审批也会由界面按新等级决定是否自动放行。
    alive/inject 的锁保护一个竞态：旧轮正在收尾时来了软中断消息，
    inject 返回 False，调用方就退回「自己排队跑一整轮」的正常路径，
    消息不会丢。
    """

    def __init__(self, approval_mode: str = "approve") -> None:
        if approval_mode not in APPROVAL_MODES:
            raise ValueError(f"approval_mode 只能是 {APPROVAL_MODES} 之一")
        self.cancel = threading.Event()
        self.done = threading.Event()
        self._inbox: list[str] = []
        self._approval_mode = approval_mode
        self._pending_approvals: dict[str, str] = {}
        self._guard = threading.Lock()
        self._alive = True

    def inject(self, text: str) -> bool:
        """塞一条待插入的消息。轮次已结束返回 False（调用方改走完整轮）。"""
        with self._guard:
            if not self._alive:
                return False
            self._inbox.append(text)
            return True

    def drain(self) -> list[str]:
        """取走全部待插入消息（Conversation 在圈界调用）。"""
        with self._guard:
            out, self._inbox = self._inbox, []
            return out

    def cancelled(self) -> bool:
        """被抢占了吗。Conversation 的检查点轮询这个。"""
        return self.cancel.is_set()

    def approval_mode(self) -> str:
        """当前执行等级。Conversation 在每个工具调用前实时读取。"""
        with self._guard:
            return self._approval_mode

    def set_approval_mode(self, mode: str) -> list[str]:
        """切换执行等级，返回这一轮正在等待的审批 id。"""
        if mode not in APPROVAL_MODES:
            raise ValueError(f"approval_mode 只能是 {APPROVAL_MODES} 之一")
        with self._guard:
            self._approval_mode = mode
            return list(self._pending_approvals)

    def register_approval(self, approval_id: str, tool_name: str) -> str:
        """登记等待中的审批，并原子地返回登记瞬间的执行等级。

        Conversation 靠这个返回值关掉一处竞态：若用户在「判断是否需要审批」
        与「登记审批」之间切到了更宽的等级，这里拿到的是新等级，可立即放行。
        """
        with self._guard:
            self._pending_approvals[approval_id] = tool_name
            return self._approval_mode

    def forget_approval(self, approval_id: str) -> None:
        """审批已结束（批准、拒绝、超时或断开），从运行时表里移除。"""
        with self._guard:
            self._pending_approvals.pop(approval_id, None)

    def close(self) -> None:
        """轮次结束（正常/出错/被打断都算）。之后的 inject 一律失败。"""
        with self._guard:
            self._alive = False
            self._pending_approvals.clear()
        self.done.set()


class _Slot:
    """一个 session_id 的槽位：串行锁 + 当前运行轮。"""

    def __init__(self) -> None:
        # 谁持有这把锁，谁在跑这个会话的轮次。queue 档就阻塞在这里等；
        # 抢占者等旧轮退场后也从这里拿到执行权。近似先来后到——
        # threading.Lock 不保证 FIFO，本地单用户够用，不粉饰。
        self.turn_lock = threading.Lock()
        # 保护 running 指针本身的读写（它和 turn_lock 是两把锁：
        # 软中断不动 turn_lock，只碰 running）。
        self._state = threading.Lock()
        self.running: RunningTurn | None = None
        # 运行轮还没真正 begin 时切了执行等级，先记住，下一次 begin 采用。
        # 这关掉「请求已发出、服务器还没建 running」的短窗口竞态。
        self._next_approval_mode: str | None = None

    def begin(self, approval_mode: str = "approve") -> RunningTurn:
        """开始一轮（调用方必须已持有 turn_lock）。"""
        with self._state:
            mode = self._next_approval_mode or approval_mode
            self._next_approval_mode = None
            running = RunningTurn(mode)
            self.running = running
        return running

    def set_approval_mode(self, mode: str) -> tuple[RunningTurn | None, list[str]]:
        """运行中改当前轮；尚未 begin 时留给下一轮。"""
        if mode not in APPROVAL_MODES:
            raise ValueError(f"approval_mode 只能是 {APPROVAL_MODES} 之一")
        with self._state:
            running = self.running
            if running is None:
                self._next_approval_mode = mode
                return None, []
            return running, running.set_approval_mode(mode)

    def finish(self) -> None:
        """结束当前轮（与 begin 配对，finally 里调用，保证一定执行）。"""
        with self._state:
            running, self.running = self.running, None
        if running is not None:
            running.close()

    def current(self) -> RunningTurn | None:
        """有没有轮次正在跑。软中断/抢占者先看这个再决定动作。"""
        with self._state:
            return self.running


class SessionManager:
    """全部会话的槽位登记处。web 进程里持有一个实例（进程级单例）。"""

    def __init__(self) -> None:
        self._guard = threading.Lock()
        self._slots: dict[str, _Slot] = {}

    def slot(self, session_id: str) -> _Slot:
        """拿一个会话的槽位，没有就建。会话数有限，不做淘汰。"""
        with self._guard:
            if session_id not in self._slots:
                self._slots[session_id] = _Slot()
            return self._slots[session_id]
