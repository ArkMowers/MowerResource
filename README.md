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

## 版权与授权

本仓库内容为游戏数据资源（webp/pkl/json），游戏素材 ©上海鹰角网络科技有限公司，仅用于学习与交流，侵删。
