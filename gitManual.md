# 项目 Git 操作手册

## 1. 克隆远程仓库
git clone "项目URL"

## 2. 开发流程

### 2.1 切换到基础功能分支
git checkout feature-ai

### 2.2 创建自己的开发分支
git checkout -b feature-ai-yourname

### 2.3 日常开发提交
git status                 # 查看修改状态
git add .                  # 添加所有修改到暂存区
git commit -m "说明本次提交内容"

### 2.4 推送前同步远程最新代码
git fetch --all            # 拉取所有远程分支信息
git merge feature-ai       # 合并基础分支的最新代码到当前分支
# 如有冲突，解决后执行：
git add .
git commit -m "解决冲突"

### 2.5 推送到远程仓库
git push origin feature-ai-yourname

### 2.6 发起 Pull Request
在 GitHub 上从 feature-ai-yourname 向 feature-ai 发起 PR

## 3. 常用命令
git branch                 # 查看本地分支
git branch -r              # 查看远程分支
git branch -a              # 查看所有分支
git checkout 分支名         # 切换分支
git branch -d 分支名        # 删除本地分支（合并后使用）
git log --oneline          # 查看提交历史