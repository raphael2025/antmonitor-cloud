# 矿场云端总览 · MCP 服务器

把云端总览的 Agent 公共 API（`GET /api/health` + `GET /api/public/*`，只读，见
[../docs/API.md](../docs/API.md) 「三、Agent 公共 API」）包装成标准 [MCP](https://modelcontextprotocol.io/)
工具，供任何支持 MCP 的 agent 运行时（Claude Code、Claude Desktop、其它 MCP
客户端）直接调用，无需手写 HTTP 请求。

只读、无写能力：本服务器只包装 `/api/health` 和 `/api/public/*` 六个查询端点，
不包装 `/api/ingest/*`、`DELETE /api/site/*`、登录等任何写/控制接口 —— 与本项目
「云端对场地只读、无反向通道」的设计原则一致（见 [../docs/DEVELOPMENT.md](../docs/DEVELOPMENT.md) §1）。

## 提供的工具

| 工具 | 对应接口 | 说明 |
|---|---|---|
| `farm_health()` | `GET /api/health` | 免 token，探活云端总览自身 |
| `farm_overview()` | `GET /api/public/overview` | 全网总览，agent 最常用 |
| `farm_site_detail(site, hours=24)` | `GET /api/public/site/{site}` | 单场地详情+趋势+客户；`site` 支持中文名，自动 URL 编码 |
| `farm_trend(hours=24, points=288)` | `GET /api/public/trend` | 多场地算力趋势线 |
| `farm_daily(site, days=14)` | `GET /api/public/daily` | 单场地按天汇总（长期保留） |
| `farm_customers()` | `GET /api/public/customers` | 跨场地客户表 |

各工具的完整字段语义（单位、`state` 枚举等）写在各自的 docstring 里，调用方
（agent）可以直接从工具描述读到，无需另外翻 API.md。

## 安装依赖

```bash
pip install -r requirements.txt
```

## 配置

两种方式，**环境变量优先**：

### 方式一：环境变量

```bash
CO_URL=http://127.0.0.1:8900
CO_PUBLIC_TOKEN=<云端 config.yaml > public_api.token>
```

`farm_health()` 不需要 token，只要 `CO_URL` 就能用；其余 5 个工具都需要
`CO_PUBLIC_TOKEN`。

### 方式二：JSON 配置文件

设置 `CO_MCP_CONFIG` 指向一个 JSON 文件，形状与 farm-report skill 的配置一致：

```json
{"url": "http://127.0.0.1:8900", "token": "<public_api.token>"}
```

本项目**不内置任何默认路径** —— 必须显式通过 `CO_MCP_CONFIG` 指定，这样这个
MCP 服务器不依赖任何单一用户的文件布局，可以给任何人独立配置。

两种方式都没配置时，工具调用会返回清晰的报错说明该怎么配，而不会让进程崩溃
或抛出裸堆栈。

## 在 Claude Code 里注册（`.mcp.json`）

在项目或用户级 `.mcp.json` 里加一条 stdio 类型的条目，例如：

```json
{
  "mcpServers": {
    "farm-cloud-overview": {
      "command": "python",
      "args": ["E:/main/mcp_server/server.py"],
      "env": {
        "CO_URL": "http://127.0.0.1:8900",
        "CO_PUBLIC_TOKEN": "<public_api.token>"
      }
    }
  }
}
```

或用配置文件方式（把 token 放文件里而不是明文写进 `.mcp.json`）：

```json
{
  "mcpServers": {
    "farm-cloud-overview": {
      "command": "python",
      "args": ["E:/main/mcp_server/server.py"],
      "env": {
        "CO_MCP_CONFIG": "C:/Users/you/.claude/farm-mcp.config.json"
      }
    }
  }
}
```

其它 MCP 客户端（Claude Desktop `claude_desktop_config.json`、以及任何支持
stdio MCP server 的 agent 运行时）用的是同一种形状（`command` + `args` + 可选
`env`），照抄上面的 `command`/`args`/`env` 即可，字段名可能略有差异但语义一致。

## 运行方式

服务器走 **stdio 传输**：由 MCP 客户端把它当子进程启动，通过标准输入输出交换
JSON-RPC 消息。不要手动 `python server.py` 后指望看到输出 —— 它会安静地等待
客户端发协议消息（stdout 被协议占用，任何调试信息只会通过工具返回值里的
`error` 字段体现，不会打印到控制台）。

## 错误处理

每个工具调用内部都做了 try/except，网络错误/超时/401/404/429/非 JSON 响应都会
转成 `{"ok": false, "error": "...说明...", "http_status": ...}` 这样的结构化
返回值，而不是让 MCP 服务器进程崩溃或把裸 Python 堆栈甩给调用方。

| 情况 | 表现 |
|---|---|
| 连不上云端总览 | `error` 里说明地址不对/服务未启动 |
| token 错误 | `http_status: 401` |
| 云端未配置 `public_api.token` | `http_status: 404`（云端 fail-closed） |
| 超过 120 次/分钟/IP | `http_status: 429` |
| 请求超过 10s | 超时报错 |

## 限制 / 已知事项

- 云端接口自带 5–30s 服务端缓存，本服务器不做二次轮询/重试，调用方也不应该
  高频调用。
- `farm_site_detail` / `farm_daily` 的 `site` 参数按 site_id 或当前显示名匹配，
  改名后旧名字可能失效（以云端 `farm_overview()` 返回的 `id`/`name` 为准）。
