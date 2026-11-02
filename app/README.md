# AgentLearn Desktop

AgentLearn 的 Tauri 桌面工作台。前端通过 `POST /chat` 和 SSE 调本机后端，
不共享 Python 代码。

界面是近黑 / 酸性绿的硬边缘控制台：左侧会话，中间执行流，右侧运行日志。
不使用营销页 Hero、堆叠卡片或柔和光晕；窄屏会把会话轨收进抽屉，只保留主舞台。

## 启动

先在另一个终端启动后端：

```bash
cd ../server
uv run python main.py desktop
```

再启动桌面端：

```bash
npm install
npm run tauri dev
```

后端目前不会由 Tauri 自动拉起。API 地址暂定为 `http://127.0.0.1:8000`，
改后端端口时要同步修改 `src/main.ts`。

## 模型设置

输入 `/config` 或按 `Ctrl+,` 打开设置抽屉。可改 provider、model ID、base URL、
max tokens、effort 和 API key；API key 留空表示保留原值。

设置通过后端 `GET /settings` / `PUT /settings` 读写 `server/config.ini`。
界面只显示密钥来源，不读取也不回显真实密钥；保存后下一次模型请求生效。

输入 `/` 打开命令面板，支持方向键、Tab 补全和 Enter 执行。`/model <id>` 可
直接切模型，`/config` 打开完整设置，`/now`、`/files`、`/boundary` 是预置
任务快捷入口；未知斜杠命令只在本地提示，不会发给模型。

流式期间也可以调档：`Alt+T` 切思考强度，本轮保持旧档、下一轮生效；
`Shift+Tab` 切执行等级，立即影响后续工具调用和已经打开的审批卡。

## 构建

```bash
npm run build
cd src-tauri && cargo check
```
