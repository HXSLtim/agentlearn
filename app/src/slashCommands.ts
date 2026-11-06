// 斜杠命令表：名称、用法、参数与补全行为，主界面与命令面板共用。

export interface SlashCommand {
  name: string;
  usage: string;
  description: string;
  group: string;
  aliases?: string[];
  takesArgument?: boolean;
}

export const SLASH_COMMANDS: SlashCommand[] = [
  {
    name: "resume",
    usage: "/resume [id]",
    description: "选择并续接历史会话",
    group: "会话",
    takesArgument: true,
  },
  {
    name: "clear",
    usage: "/clear",
    description: "清空当前上下文并开新会话",
    group: "会话",
  },
  {
    name: "new",
    usage: "/new",
    description: "新开一个空会话",
    group: "会话",
  },
  {
    name: "memory",
    usage: "/memory [内容]",
    description: "往跨会话记忆记一条；不带参数查看",
    group: "会话",
    takesArgument: true,
  },
  {
    name: "find",
    usage: "/find [关键词]",
    description: "定位当前会话里的一条已渲染消息",
    group: "会话",
    takesArgument: true,
  },
  {
    name: "status",
    usage: "/status",
    description: "查看当前会话与运行状态",
    group: "运行",
  },
  {
    name: "trace",
    usage: "/trace",
    description: "打开当前会话的 span 树与回答配对",
    group: "运行",
  },
  {
    name: "stop",
    usage: "/stop",
    description: "在下一个检查点停止当前运行轮",
    group: "运行",
  },
  {
    name: "retry",
    usage: "/retry",
    description: "重跑上一条失败的请求，不新增用户消息卡片",
    group: "运行",
  },
  {
    name: "steering",
    usage: "/steering [mode]",
    description: "切换 queue / cooperative / preemptive",
    group: "运行",
    takesArgument: true,
  },
  {
    name: "effort",
    usage: "/effort [level]",
    description: "切换思考等级；不带参数循环",
    group: "运行",
    takesArgument: true,
  },
  {
    name: "approval",
    usage: "/approval [mode]",
    description: "切换 approve / auto / yolo",
    group: "运行",
    takesArgument: true,
  },
  {
    name: "plan",
    usage: "/plan <task>",
    description: "先只读探索，产出计划经人工确认后执行",
    group: "运行",
    takesArgument: true,
  },
  {
    name: "capabilities",
    usage: "/capabilities",
    description: "查看工具、MCP 健康、运行时与沙箱事实",
    group: "系统",
  },
  {
    name: "transcript",
    usage: "/transcript [mode]",
    description: "切换 normal / thinking / verbose 三档正文视图",
    group: "运行",
    takesArgument: true,
  },
  {
    name: "copy",
    usage: "/copy",
    description: "复制最近一条完整回答",
    group: "系统",
  },
  {
    name: "export",
    usage: "/export",
    description: "把当前会话导出为 Markdown",
    group: "系统",
  },
  {
    name: "model",
    usage: "/model <id>",
    description: "切换运行模型；不带参数打开设置",
    group: "运行",
    takesArgument: true,
  },
  {
    name: "config",
    usage: "/config",
    description: "打开 config.ini 模型设置",
    group: "运行",
    aliases: ["settings"],
  },
  {
    name: "now",
    usage: "/now",
    description: "触发 now_time 示例",
    group: "任务",
  },
  {
    name: "files",
    usage: "/files",
    description: "查看 sandbox 目录",
    group: "任务",
  },
  {
    name: "boundary",
    usage: "/boundary",
    description: "触发 reason / act / report 示例",
    group: "任务",
  },
  {
    name: "help",
    usage: "/help",
    description: "显示全部命令",
    group: "系统",
  },
];

export const SLASH_COMMANDS_BY_NAME = new Map<string, SlashCommand>();
for (const command of SLASH_COMMANDS) {
  SLASH_COMMANDS_BY_NAME.set(command.name, command);
  for (const alias of command.aliases ?? []) {
    SLASH_COMMANDS_BY_NAME.set(alias, command);
  }
}
