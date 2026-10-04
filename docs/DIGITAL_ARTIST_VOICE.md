# 艺人声音：Suno 绑定与本地参考

Suno 中的 Persona/Voice 是账户内保存的生成选择。本地 WAV/MP3 是试听、比对和归档证据；下载音频不会导出 Persona 模型，也不会自动让其他工具具备声音克隆能力。

## 保存已有声音

已按用户确认绑定：

| 艺人 | Suno Voice | 本地样本 |
| --- | --- | --- |
| Fati | https://suno.com/voice/43a8e729-d9eb-4e6c-9f84-0c940d55b33b | 待导入 |
| Amor | https://suno.com/voice/d1ad9edd-9f5e-4221-ba18-5199b5749fcb | 待导入 |

先确认链接对应的艺人。不要从链接顺序推断归属。记录后无需重做试音：

```bash
python -m lib.artist_profile bind-suno amor --kind voice --url https://suno.com/voice/<voice-id> --confirm
python -m lib.artist_profile check amor --voice-route suno_web
```

也可用 `--name` 保存账户内可识别的名称，用 `--source-song-url` 保存源歌曲链接。`--confirm` 只用于用户已经确认的对应关系。网页绑定保存在 `voice.provider_binding`，不会写入旧的 `persona_id` API 字段。

## 下载到本地

1. 在 Suno 网页的 Library/Workspace 找到创建该 Voice/Persona 的源歌曲，或者用该选择生成的已认可歌曲。
2. 打开歌曲的 `…` 菜单 → `Download`。下载的是歌曲音频，而非 Voice 页面或模型。
3. 可选 MP3；官方说明 WAV 在网页端向 Pro/Premier 开放。若歌曲有可下载的人声 stem，优先保留人声 stem 用于音色比较；完整混音也可保存。
4. 将下载文件导入对应艺人。只有已选定、已听过的声音才使用 `--approve`：

```bash
python -m lib.artist_profile import-voice amor /Users/geesh/Downloads/amor.wav --approve --notes "用户选定的 Amor 声音参考"
python -m lib.artist_profile check amor
```

导入器使用 ffprobe 检查音频，按内容哈希保存文件，记录时长、SHA-256、批准日期及来源；拒绝覆盖不同内容的已有参考。`--source-url` 可记录源歌曲。请备份 `artists/<id>/voice/`，导出的公共增量补丁不携带这些个人音频。

## 生成时怎么传递

| 路径 | 支持范围 |
| --- | --- |
| Suno 网页 | 在自己的账户中手动选择保存的 Persona/Voice，记录实际使用的模型，再下载结果导入 MV 项目 |
| 当前 `suno_music` | 第三方 sunoapi.org 的文本生成适配器；没有实现网页 Voice/Persona 或参考音频传递，相关参数会被明确拒绝 |
| 本地参考音频 | 试听与 A/B 比对；只有所选提供方的工具明确支持参考音频、参数映射和输入格式时，才可用于生成 |

双人合唱的风格描述、歌手标签不会保证两条声线。如果当前网页不能同时选择两条声音，需要先讨论并批准分别生成声部再组合的方案。不要默认替换任何已选声音。

## 状态含义

`pending_audition` 表示尚未绑定；`provider_bound` 表示网页声音已确认；`locked` 表示本地批准音频也已留存。检查结果同时展示网页绑定、本地样本及生成能力，不把它们合成一个含混的 ready。

默认检查要求本地参考。`--voice-route suno_web` 检查网页绑定，`--voice-route visual_only` 仅适用于已有批准成品歌曲、只需制作画面的情况。当前 `--voice-route suno_music` 不满足固定声音生成要求。

官方说明（核验日期：2026-10-04）：[下载步骤](https://help.suno.com/en/articles/2409921)、[下载格式](https://help.suno.com/en/articles/13926081)、[Persona](https://help.suno.com/en/articles/3484161)、[Voices FAQ](https://help.suno.com/en/articles/11362433)。新 Voices 与 Style Personas 的界面和模型支持不同，使用时以账户实际提供的功能为准。
