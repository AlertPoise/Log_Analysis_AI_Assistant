# 项目开发规范

## 技术栈
- 前端：[如 React 18 + TypeScript 5 + Vite + TailwindCSS]
- 状态管理：[如 Zustand + React Query]
- 路由：[如 React Router v6]
- 构建工具：[如 Vite 5]

## 代码规范
- 组件命名：PascalCase，文件名与组件名一致
- 文件命名：组件用 .tsx，纯逻辑用 .ts，样式用 .module.css
- 导出方式：默认导出组件，命名导出工具函数和类型
- 注释要求：复杂逻辑必须注释，JSDoc 用于公共函数和组件 Props

## 架构约定
- 目录结构：
  - src/components/    # 可复用 UI 组件
  - src/features/      # 业务功能模块
  - src/hooks/         # 自定义 Hook
  - src/utils/         # 工具函数
  - src/types/         # 类型定义
- 状态管理：页面级状态用 Zustand，跨页面共享用 React Query 缓存
- API 调用：统一在 src/api/ 下定义，使用 axios 实例，配置拦截器

## AI 协作约定
- 代码输出时，只输出修改的部分，不要输出完整文件
- 优先使用项目已有的工具函数和组件，不要重复造轮子
- 新增依赖时，说明原因和版本建议
- 对于复杂改动，先给出方案再写代码

## 常用命令
- 开发：npm run dev
- 构建：npm run build
- 类型检查：npm run type-check
- 代码格式化：npm run format