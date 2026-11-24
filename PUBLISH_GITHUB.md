# 发布到 GitHub

当前目录已经是可提交的源码目录。`dist/`、`build/`、`config.json`、`data/` 已在 `.gitignore` 中排除。

## 方式一：网页上传（无需安装 Git）

1. 登录 GitHub，新建仓库，例如 `GXUSTPower`，不要勾选初始化 README。
2. 解压 `dist/GXUSTPower-1.0.0-source.zip`。
3. 在网页点「uploading an existing file」，把解压后的全部文件拖进去，提交。
4. 打开仓库 Releases → Draft a new release：
   - Tag：`v1.0.0`
   - 上传 `dist/GXUSTPower-1.0.0-Setup.exe`、`GXUSTPower-1.0.0.msi`、`GXUSTPower-1.0.0-portable.exe`
   - 发布说明可用 `RELEASE_NOTES.md`

## 方式二：命令行

```powershell
winget install Git.Git
git init
git add .
git commit -m "GXUST电费助手 1.0.0"
git branch -M main
git remote add origin https://github.com/smg123233/GXUSTPower.git
git push -u origin main
```

推送时使用 GitHub 的 **Personal Access Token** 或 SSH Key，不要使用账号密码（GitHub 已不支持密码推送）。

## 安全提示

- 不要在源码、提交记录或聊天里保存 GitHub 密码；
- 若密码曾发送到聊天，请立即修改密码并开启两步验证；
- 发布前确认 `config.json`、`data/` 没有被提交（`.gitignore` 已排除）。
