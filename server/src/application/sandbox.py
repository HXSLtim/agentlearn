"""sandbox 常量：bash / jobs / screenshot 共用的执行环境基线。

原来长在 tools.py，jobs.py 反过来 import 它们，tools 的 REGISTRY 又
引用 jobs 的函数——一个只能按特定顺序导入的环（jobs 先导入就炸，
离线测试抓的）。常量谁都要、谁都不该拥有，独立成模块，环就没了。
"""
from __future__ import annotations

import os
from pathlib import Path

# bash / 后台任务的工作目录。注意：这只是「起跑点」，不是安全边界——
# 命令用绝对路径或 ../ 就能走到任何地方（离线实测 cat ../../config.ini
# 能读到）。学习项目接受这一点：模型反正是我们自己的，工具进程跑在
# 我们自己的账号下，和自己开终端没有更多权限。真要对外服务，隔离是
# 容器/独立用户的事（记在 TODO / D17）。
# 目录固定在 server/data/sandbox，好歹让 ls 出来的默认视图是干净的，
# 也避免工具的临时文件把项目目录弄乱。data/ 整个在 gitignore 里。
SANDBOX = Path("data") / "sandbox"

# 子进程只给这一个环境变量：PATH 得留着（不然 python3、find 都找不到），
# 其余全部剥掉。原因：进程环境里可能有 LLM_API_KEY、代理地址这类值，
# 而工具输出会原样进模型上下文并存进 JSONL——一条 `env` 就把密钥送进
# 对话历史了。这不是沙箱（上面说了没有隔离），只是缩小波及面：
# 注入的命令读不到进程环境。副作用是 HOME/临时变量也没了，
# 一次性脚本基本不受影响。
CHILD_ENV = {"PATH": os.environ.get("PATH", "/usr/bin:/bin")}
