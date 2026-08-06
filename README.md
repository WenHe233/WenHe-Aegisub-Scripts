# WenHe 的 Aegisub 脚本

文何的 Aegisub Automation 脚本仓库，可通过 DependencyControl 安装和自动更新。

**AI使用说明：本仓库的绝大多数内容都是AI生成的，太好用了AI（**

## 收录脚本

| 脚本 | 命名空间 | 版本 | 用途 |
| --- | --- | --- | --- |
| 文字渐入渐出 | `wenhe.TextFadeInOut` | 2.1.0 | 用分层 `clip` 生成带柔和边缘的文字渐入、渐出效果 |
| 套用平移 (`\move`) | `wenhe.ApplyMove` | 1.0.0 | 将参考行的 `\move` 方向、距离和时间套用到其它选中行 |

## 使用 DependencyControl 安装

自定义源地址：

```text
https://raw.githubusercontent.com/WenHe233/WenHe-Aegisub-Scripts/main/DependencyControl.json
```

1. 关闭 Aegisub，打开用户配置文件：
   `%APPDATA%\Aegisub\config\l0.DependencyControl.json`。
2. 先备份该文件。DependencyControl 0.7.0 及以上把源列表放在
   `config.feeds.extraFeeds`；把上面的地址加入该数组，并保留其中已有的其它源，例如：

   ```json
   {
     "$schema": "https://raw.githubusercontent.com/TypesettingTools/DependencyControl/publish/schemas/config/v0.7.0.json",
     "config": {
       "feeds": {
         "extraFeeds": [
           "https://raw.githubusercontent.com/TypesettingTools/DependencyControl/publish/DependencyControl.json",
           "https://raw.githubusercontent.com/WenHe233/WenHe-Aegisub-Scripts/main/DependencyControl.json"
         ]
       }
     }
   }
   ```

   你的配置通常还包含已安装宏和模块的记录，不要用这个精简示例覆盖整个文件。
   DependencyControl 0.6.x 及更早版本使用旧的 `config.extraFeeds` 路径。
3. 启动 Aegisub，打开「自动化 → DependencyControl → Install Script」。
4. 从 Automation Scripts 中选择需要的脚本并安装，然后重新扫描自动化目录或重启 Aegisub。

## 手动安装

从 [`macros`](./macros) 下载所需 `.lua` 文件，放入：

```text
%APPDATA%\Aegisub\automation\autoload
```

重新扫描自动化目录或重启 Aegisub。没有安装 DependencyControl 时，脚本会自动回退到 Aegisub 原生宏注册方式。

### 从旧文件迁移

安装新版本前，请从 `autoload` 删除旧文件：

- `wenhe.SoftWipe.lua`
- `apply-move.lua`

它们从未受新命名空间的 DependencyControl 管理，无法由安装器自动移除；保留旧文件会造成重复菜单。

## 维护和自动发布

脚本位于 `macros/`，文件名必须严格等于 `<script_namespace>.lua`。每个脚本必须在独立单行中使用双引号定义：

```lua
script_name        = "示例宏"
script_description = "示例说明"
script_author      = "WenHe"
script_version     = "1.0.0"
script_namespace   = "wenhe.Example"
script_url         = "https://github.com/WenHe233/WenHe-Aegisub-Scripts"
```

日常发布流程：

1. 修改脚本。
2. 将 `script_version` 递增为新的三段式语义版本。
3. 用清晰的 Git 提交标题说明变化并推送到 `main`。
4. GitHub Actions 自动更新 `DependencyControl.json` 的版本、UTC 日期和 SHA-1，提交回 `main`，创建 `<namespace>-v<version>` 标签及 GitHub Release，并验证标签中的 raw 文件。

如果脚本内容改变但版本未变、版本倒退、元数据不完整、文件名和命名空间不一致，发布会失败。删除脚本也会失败，必须先设计 DependencyControl 下架或迁移方案。

### 新增脚本

把符合上述约定的新 `.lua` 文件加入 `macros/` 并推送即可。Action 会根据 Lua 元数据自动创建 feed 条目并首发。

普通单文件宏不需要额外配置。若新脚本需要 DependencyControl 安装第三方模块，可增加 `packages/<namespace>.json`：

```json
{
  "release": {
    "requiredModules": [
      {
        "moduleName": "example.Module",
        "version": "1.0.0",
        "feed": "https://example.com/DependencyControl.json"
      }
    ]
  }
}
```

包配置只允许补充 `package` 和 `release` 字段，不能覆盖由 Lua 和发布流程管理的名称、作者、版本、日期、文件及 changelog。

## 本地校验

```powershell
python -m pip install -r requirements-ci.txt
python -m unittest discover -s tests -v
python tools/sync_dependency_control.py --base 0000000000000000000000000000000000000000 --head HEAD
```

同步工具会使用官方 DependencyControl 0.4.0 JSON Schema 验证生成结果。

## 许可证

本仓库以 [GNU General Public License v3.0 only](./LICENSE) 发布。
