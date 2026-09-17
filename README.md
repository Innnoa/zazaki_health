# zazaki_health

## 1. 项目简介

从手机 Samsung Health 读取 Watch 7 同步数据，经 PC 接收端落盘后，由 Python 分析器生成每日 HTML 报告（含 LLM 解读）与 14 天整体能力分析，并在本地 Web 看板中回看。

链路：手机 Samsung Health → PC receiver → Python analyzer → 每日 HTML 报告 + LLM 解读 + 14 天能力分析 → 本地 Web 看板。

## 2. 架构 / 数据流

```
Watch 7 (SM-L300)
  │ 蓝牙同步
  ▼
手机 Samsung Health（S25 国行）
  │ Samsung Health Data SDK 只读
  ▼
HealthReader app（com.zazaki.healthreader）
  │ 逐日导出 YYYYMMDD_sleep_hr.json
  │ HTTP POST http://<PC>:8899/upload/<YYYYMMDD>_sleep_hr.json
  ▼
zazaki_health_receiver（phone_http_receiver_tcp.ps1，:8899）
  │ 落盘 data\<YYYYMMDD>_sleep_hr.json，并按日触发分析
  ▼
analyzer（analyzer.py + stats.py + llm.py + report_builder.py / analysis14.py）
  │ 写 reports\<YYYYMMDD>_report.html / <YYYYMMDD>_analysis14.html
  ▼
dashboard.py（:8890）本地 Web 看板
```

- receiver 端口 8899：手机上传入口。
- dashboard 端口 8890：本地/局域网看板入口。

## 3. 目录结构

```
health_reader/                  Android app + Python 分析/看板 + 服务脚本 + 规格与计划
├── app/                        Android 采集 app（applicationId com.zazaki.healthreader）
├── analyzer/                   Python 分析器（仅标准库）
├── dashboard/                  Python 本地 Web 看板（仅标准库）
├── health_service.ps1          服务控制脚本（start/stop/restart/status）
├── launch_hidden.vbs           隐藏窗口启动器（供服务控制脚本调用）
└── *_SPEC.md / *_PLAN.md       规格与实现计划
zazaki_health_receiver/         PC 接收端源码
└── phone_http_receiver_tcp.ps1 HTTP/TcpListener 接收端
pc_receiver/                    旧版接收端（保留）
```

Windows 部署：

- `C:\Users\hzj\zazaki_health\{analyzer,dashboard}` — analyzer 与 dashboard 的运行副本。
- `C:\Users\hzj\zazaki_health_receiver\data` — 接收端数据目录（`YYYYMMDD_sleep_hr.json` 与 `reports\`）。

## 4. 环境要求

- Android app：JDK 17 + Android SDK（compileSdk/targetSdk 36，minSdk 29）；仓库自带 Gradle wrapper（8.14.3）、AGP 8.11、Kotlin 2.1.0，无需本机安装 Gradle。
- analyzer / dashboard：Windows + Python（仅标准库，无第三方依赖）。
- 开发：WSL。

## 5. 快速开始

```bash
# 1. 构建 APK（在 health_reader/ 下）
./gradlew assembleDebug

# 2. 部署到 Windows
#    analyzer/ 与 dashboard/ 复制到 C:\Users\hzj\zazaki_health\
#    zazaki_health_receiver/ 复制到 C:\Users\hzj\zazaki_health_receiver\

# 3. 启动服务（WSL）
health start
```

说明：analyzer 的 LLM 解读依赖 `analyzer/config.json` 中的 API key；未配置或调用失败时自动降级为模板解读。

## 6. 服务控制

```bash
health <start|stop|restart|status> [all|dashboard|receiver]
```

- WSL 包装命令 `~/.local/bin/health` 转发到 Windows 侧 `health_service.ps1`。
- 无参数等价于 `status all`。
- 服务不自动启动：仅在命令启动后运行。
- 直接调用（Windows）：`powershell -ExecutionPolicy Bypass -File health_service.ps1 start all`。

## 7. 报告与看板

- 每日报告：`<data-dir>\reports\<YYYYMMDD>_report.html`
- 14 天能力分析：`<data-dir>\reports\<YYYYMMDD>_analysis14.html`，能力分留存 `<data-dir>\reports\analysis14_scores.json`
- 看板地址：`http://127.0.0.1:8890/`（监听 `0.0.0.0:8890`，手机可访问 `http://192.168.137.1:8890`）
- 看板路由：
  - `GET /` — 单页看板
  - `GET /report/<YYYYMMDD>` — 指定日期每日报告
  - `GET /analysis14` — 最新 14 天报告
  - `GET /analysis14/<YYYYMMDD>` — 指定日期 14 天报告
  - `GET /api/status`、`GET /api/calendar?month=YYYY-MM`、`GET /api/day?date=YYYYMMDD`
  - `GET /api/analysis14/latest`、`GET /api/analysis14/scores`
  - `POST /api/analyze` — 手动触发指定日期的分析

## 8. 隐私 / 数据

- 健康数据只保存在本地 PC 的数据目录；唯一外发是 analyzer 调用 LLM API（OpenAI 兼容接口）时发送的统计值。
- `config.json` 保存 LLM API key，已由 `.gitignore` 排除；仓库不包含任何 key。

## 9. 已知边界

- 无 HRV：心率约 1 次/分采样，无法还原 HRV/SDNN/RMSSD。
- 血氧约 10 分钟/点：不计算 ODI/T90。
- 深睡/REM 为设备分期估算。
- 国行固件 BOOT_COMPLETED 对第三方应用不投递：全天模式在设备重启后需手动重开。
