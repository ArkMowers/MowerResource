# MowerResource

Mower 的资源包发布仓库。

GitHub Actions 每小时检测上游游戏数据变化，有更新则生成资源包并发布到 Releases（`resource.zip`），版本元数据见仓库根 `version.json`。手动出包走 Actions 的 workflow_dispatch。

生成流水线见 `.github/scripts/resource_build.py`，本仓库只存版本元数据与发布产物，资源源文件不落库。

## 上游数据源

| 用途 | 上游仓库 | 分支 / 路径 |
| --- | --- | --- |
| 游戏数据 excel（活动/卡池/物品/关卡/技能/基建…） | [ArknightsAssets/ArknightsGamedata](https://github.com/ArknightsAssets/ArknightsGamedata) | `cn/gamedata/excel` |
| 物品与干员头像图片 | [yuanyan3060/ArknightsGameResource](https://github.com/yuanyan3060/ArknightsGameResource) | `item`、`avatar` |
| 加工站/专精合成配方 | [Arknights-yituliu/frontend-v2-plus](https://github.com/Arknights-yituliu/frontend-v2-plus) | `dev` 分支 `src/static/json/material/composite_table.v2.json` |
| 数据快照时间戳（`version.json` 的 `last_updated`） | [yuanyan3060/ArknightsGameResource](https://github.com/yuanyan3060/ArknightsGameResource) | 仓库根 `version` |
| 识别用字体 | [ArkMowers/MowerFonts](https://github.com/ArkMowers/MowerFonts) | 私有仓库，部署密钥只读取 |
| 生成脚本与 `arknights_mower` 依赖 | [ArkMowers/arknights-mower](https://github.com/ArkMowers/arknights-mower) | `alpha` 分支 |

`ArknightsAssets/ArknightsGamedata` 的 `cn/gamedata/excel` 随国服更新（最近提交 `Arknights update cn`），活动与卡池取自该 excel，避免旧源停更导致的「活动/卡池对不上」问题。

## 专精干员分支

资源包内 `arknights_mower/data/skill_data.json` 的 `characters[char_id]` 保留上游
`character_table.json` 的 `subProfessionId`，例如桑葚为 `wandermedic`（行医）。
`profession` 仍表示八大职业；分支字段为原始 ID，不翻译、不从技能描述推断。
该表沿用现有专精数据筛选范围，不是全量干员目录。

字段由主仓库 `alpha` 的 `auto_get_res_new.py` 在生成时写入，参与现有资源内容哈希，
无需新增打包文件。发布前逐项核对分支与本次拉取的游戏数据是否一致；缺失或不一致时终止发布。
新增字段兼容只读取旧字段的客户端。

上线顺序：先合入主仓库生成脚本的字段提取，再合入本仓库校验；随后手动运行
`workflow_dispatch` 生成资源包。仅修改生成脚本不会触发每小时的游戏源变化检查。
本地校验测试：`python -m unittest discover -s .github/scripts/tests -p 'test_*.py'`。

## 版权与授权

本仓库内容为游戏数据资源（webp/pkl/json），游戏素材 ©上海鹰角网络科技有限公司，仅用于学习与交流，侵删。

### 字体子集自动扩充

主仓生成器在绘制姓名和技能模板前检查字体实际字符映射，缺字时从
MowerFonts 的 `fonts/NotoSansHans-Medium.otf` 和
`fonts/SourceHanSansCN-Medium.ttf` 自动扩充子集。管线通过
`MOWERFONTS_DIR` 传入字体检出目录；源字体缺失、指纹不符
或源字体也缺字时，构建明确失败。完整字体只用于生成，不进入资源包。
字体或脚本单独更新后，使用 `workflow_dispatch` 手动出包。

## OTA 增量更新

每次资源发布同时保留 `resource.zip`，并提供含附件大小和 SHA-256 的
`resource-update.json`。生成器最多从四个保留的资源版本构建直达当前版本的
`resource-ota_<起点>_to_<目标>.zip`；包内仅存新增或变化的文件及完整目标
文件清单。差异包达到完整包的百分之八十五时不发布。

支持 OTA 的 Mower 优先下载匹配当前资源版本的较小增量包，逐文件验证并重建
完整资源目录。增量不可用时回退同一目标 Release 的整包；旧版 Mower 继续
下载 `resource.zip`。所有附件上传到草稿 Release 后再公开，避免客户端读到
缺失附件的索引。手动 OTA 保持离线，当前资源版本与起点不符时拒绝安装。

资源编解码协议随主仓 `arknights_mower/utils/resource_ota.py` 维护。
发布端单元测试在主仓检出可用时使用同一模块；本地用
`MOWER_OTA_TEST_ROOT` 指向该检出。主仓尚无模块时，发布流程只提供整包及其摘要索引。
