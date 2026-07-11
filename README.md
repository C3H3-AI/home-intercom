# 家庭广播 (Home Intercom)

> **移植声明**：本集成是 [mdj2812/home-intercom](https://github.com/mdj2812/home-intercom) 的**移植版本**（非 fork），核心逻辑参考原项目，但代码完全重写为 HA 原生集成架构。

将原项目改造成 **Home Assistant 原生自定义集成**。手机「按住说话」→ 家里音箱实时出声——真对讲，不是 TTS 文字播报。

## ✨ 特性

- **HA 原生集成** — 跑在 HA 内部，无额外容器、无 HTTPS 反代、无 HA_TOKEN 暴露
- **侧边栏面板** — 打开即用，美观 PWA 对讲界面
- **自动发现可播音箱** — 扫描所有 media_player，只显示能播的
- **小米 MIoT 自动适配** — 小米音箱自动走 `send_command(player_play_url)`，无需特殊配置
- **内网音频传输** — 音箱通过内网拉取录音，不暴露公网
- **三档自动停止** — Music Assistant / 现代播放器 / 基础播放器定时暂停
- **面板内设置** — ⚙️ 点选音箱配房间，不用改文件

## 安装

### HACS 安装（推荐）

1. 添加自定义仓库：`https://github.com/C3H3-AI/home-intercom`，类别选「集成」
2. 搜索「Home Intercom」安装
3. 重启 HA
4. **设置 → 集成 → 添加家庭广播**（零配置）

### 手动安装

把 `custom_components/home_intercom/` 放到 HA 配置目录的 `custom_components/home_intercom/`，重启 HA。

## 快速开始

1. 重启后侧边栏出现 **📢 家庭广播** 面板
2. 点击打开→⚙️设置→选一台音箱→保存
3. 回到主面板，按住说话→松开发送→音箱出声

> 第一次使用时，可以先在电脑上按住小爱 Pro 卡说一句话，试试能不能出声。

## 配置房间

打开侧边栏面板 → ⚙️ → **添加房间** → 从下拉列表选音箱 → 保存

系统自动判断：
- 小米 MIoT 平台的音箱 → 走 `send_command(player_play_url)`
- 其他标准音箱 → 走 `play_media(announce=True)`
- 不支持对讲的实体 → 不在下拉列表中显示

也可以直接在 **HA 设置 → 集成 → 家庭广播 → 选项** 中编辑 JSON：

```json
{
  "living":     {"name": "Living Room", "entity": "media_player.living_room_speaker"},
  "xiaomi_pro": {"name": "Xiaomi Pro",  "entity": "media_player.xiaomi_pro_speaker", "play_method": "xiaomi_miot"}
}
```

> 不需要手动配 `play_method`，面板里选好音箱保存时会自动检测。

## 工作原理

```
手机 PWA → HA REST API (/api/home_intercom/record)
         → 保存 WAV 到 HA 配置目录
         → 根据音箱能力选择播放方式：
           1. Music Assistant → play_announcement（自停）
           2. 现代播放器 → play_media(announce=True)（自停）
           3. 基础播放器（小爱等）→ play_media + 定时器暂停
           4. 小米 MIoT → send_command(player_play_url) + 定时器暂停
         → 音箱通过内网拉取音频播放
```

## 与其他方案对比

| 特性 | 原版 home-intercom | 本集成 |
|------|-------------------|--------|
| 部署方式 | 独立 Docker 容器 | HA 原生集成 |
| HTTPS 反代 | 需要（外网访问） | 无需（复用 HA 域名） |
| HA_TOKEN | 需要配环境变量 | 不需要 |
| 侧边栏入口 | ❌ | ✅ |
| 自动发现音箱 | ❌ 用户手动配 | ✅ 自动扫描过滤 |
| 小米 MIoT 兼容 | ❌ 标记为不支持 | ✅ 自动切换 send_command |
| 设置界面 | ❌ 改 rooms.json 文件 | ✅ 面板内点选 |

## API 端点（供高级用户参考）

| 端点 | 方法 | 说明 |
|------|------|------|
| `/api/home_intercom/version` | GET | 版本号 |
| `/api/home_intercom/rooms/status` | GET | 各音箱在线状态 |
| `/api/home_intercom/record?target=<房间ID>` | POST | 上传录音并播放 |
| `/api/home_intercom/audio/<文件名>` | GET | 下载录音文件 |
| `/api/home_intercom/config` | GET/POST | 读写房间配置 |
| `/api/home_intercom/www/` | GET | 静态资源（PWA） |

## 已知限制

- `/api/home_intercom/*` 视图 `requires_auth=False`，仅适合可信 LAN 环境（与原版一致）
- iframe 面板的「添加到主屏幕」能力受限，但 push-to-talk 功能不受影响

## 更新日志

### v1.1.1
- 修复设置面板「房间名」：房间名改为可独立编辑输入框，保存时真正写回（不再被设备名覆盖）
- 房间名自动采用 HA 设备分配的房间(area)：后端 ConfigView 返回每个 media_player 的 area，前端打开设置即预填
- 修复 area 解析盲区：HA 房间挂在设备层，实体通过 device_id 继承；补充 device_registry 回退（实体级 area_id → 否则设备级 area_id），小米音箱正确带出「客厅」
- 顺手修复前端 option 的 data-name / data-platform 未转义的历史隐患
- 新增 HA 注册表与实体-房间解析研究报告（docs/）

### v1.1.0
- 新增 `home_intercom.announce` 服务：支持 room 指定房间 / url 音频直播 / message TTS 语音广播

### v1.0.0
- 原生 HA 集成首发：侧边栏 PWA 对讲面板、自动发现可播音箱、小米 MIoT 自动适配、面板内房间配置
