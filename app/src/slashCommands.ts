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
    group: "SESSION",
    takesArgument: true,
  },
  {
    name: "clear",
    usage: "/clear",
    description: "清空当前上下文并开新会话",
    group: "SESSION",
  },
  {
    name: "new",
    usage: "/new",
    description: "新开一个空会话",
    group: "SESSION",
  },
  {
    name: "memory",
    usage: "/memory [内容]",
    description: "往跨会话记忆记一条；不带参数查看",
    group: "SESSION",
    takesArgument: true,
  },
  {
    name: "find",
    usage: "/find [关键词]",
    description: "定位当前会话里的一条已渲染消息",
    group: "SESSION",
    takesArgument: true,
  },
  {
    name: "status",
    usage: "/status",
    description: "查看当前会话与运行状态",
    group: "RUNTIME",
  },
  {
    name: "trace",
    usage: "/trace",
    description: "打开当前会话的 span 树与回答配对",
    group: "RUNTIME",
  },
  {
    name: "stop",
    usage: "/stop",
    description: "在下一个检查点停止当前运行轮",
    group: "RUNTIME",
  },
  {
    name: "retry",
    usage: "/retry",
    description: "重跑上一条失败的请求，不新增用户消息卡片",
    group: "RUNTIME",
  },
  {
    name: "steering",
    usage: "/steering [mode]",
    description: "切换 queue / cooperative / preemptive",
    group: "RUNTIME",
    takesArgument: true,
  },
  {
    name: "effort",
    usage: "/effort [level]",
    description: "切换思考等级；不带参数循环",
    group: "RUNTIME",
    takesArgument: true,
  },
  {
    name: "approval",
    usage: "/approval [mode]",
    description: "切换 approve / auto / yolo",
    group: "RUNTIME",
    takesArgument: true,
  },
  {
    name: "plan",
    usage: "/plan <task>",
    description: "Read-only exploration, then a human-confirmed plan",
    group: "RUNTIME",
    takesArgument: true,
  },
  {
    name: "capabilities",
    usage: "/capabilities",
    description: "Show tools, MCP health, runtime and sandbox facts",
    group: "SYSTEM",
  },
  {
    name: "transcript",
    usage: "/transcript [mode]",
    description: "切换 normal / thinking / verbose 三档正文视图",
    group: "RUNTIME",
    takesArgument: true,
  },
  {
    name: "copy",
    usage: "/copy",
    description: "复制最近一条完整回答",
    group: "SYSTEM",
  },
  {
    name: "export",
    usage: "/export",
    description: "把当前会话导出为 Markdown",
    group: "SYSTEM",
  },
  {
    name: "model",
    usage: "/model <id>",
    description: "切换运行模型；不带参数打开设置",
    group: "RUNTIME",
    takesArgument: true,
  },
  {
    name: "config",
    usage: "/config",
    description: "打开 config.ini 模型设置",
    group: "RUNTIME",
    aliases: ["settings"],
  },
  {
    name: "now",
    usage: "/now",
    description: "触发 now_time 示例",
    group: "TASK",
  },
  {
    name: "files",
    usage: "/files",
    description: "查看 sandbox 目录",
    group: "TASK",
  },
  {
    name: "boundary",
    usage: "/boundary",
    description: "触发 reason / act / report 示例",
    group: "TASK",
  },
  {
    name: "help",
    usage: "/help",
    description: "显示全部命令",
    group: "SYSTEM",
  },
];

export const SLASH_COMMANDS_BY_NAME = new Map<string, SlashCommand>();
for (const command of SLASH_COMMANDS) {
  SLASH_COMMANDS_BY_NAME.set(command.name, command);
  for (const alias of command.aliases ?? []) {
    SLASH_COMMANDS_BY_NAME.set(alias, command);
  }
}
