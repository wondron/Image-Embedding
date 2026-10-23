# 食材图像工作台

React + TypeScript + Vite 前端。当前优先接入已有 Global 图像搜索，后续数据管理功能保留独立页面，不新增后端接口。

## 本地运行

需要 Node.js 22.12+ 或 24，以及 npm。

```bash
cd frontend
npm install
```

Windows 如因本机 npm 缓存目录权限遇到 `EPERM`，可在 PowerShell 使用临时缓存：`npm.cmd install --cache "$env:TEMP\food-atlas-npm-cache"`，无需修改全局 npm 配置。

将 `.env.example` 复制为 `.env.local`，按需调整后端地址，然后启动：

```bash
npm run dev -- --host 0.0.0.0
```

打开终端显示的地址，默认 `http://localhost:5173`。后端需单独启动；开发服务器默认将 `/api` 转发到 `http://127.0.0.1:8319`，与当前后端 Compose 的端口映射一致。

| 配置 | 用途 |
| --- | --- |
| `VITE_API_PROXY_TARGET` | Vite 开发代理的后端地址，修改后需重启开发服务器 |
| `VITE_API_BASE_URL` | 可选的浏览器 API 地址；留空使用同源 `/api`，跨域需要后端允许 CORS |

界面中的接口设置可覆盖浏览器 API 地址，保存在当前浏览器的 `localStorage`。`VITE_*` 配置会进入前端构建产物，不应包含密钥。

## 当前功能

- 上传查询图片或输入图片 URL，调用已有搜索接口；显示请求状态、错误与空结果。
- 支持网格／列表切换，展示结果图片和后端返回的相似度；原图加载失败时尝试缩略图，详情弹窗按检索、预览、来源分区显示。
- 当前结果集支持本地分页；只有接口提供类别时才能按类别筛选，不会从标题推测类别。
- 下载结果图片。第三方图片地址允许跨域读取时可直接保存；遇到 CORS 或防盗链限制时降级为打开图片，可在新页面手动另存。未增加后端下载代理。

预留图片数据、上传与批量导入、入库任务、食材名称/类别/属性管理页面。文件队列和食材草稿仅用于浏览器本地交互，不会上传或写入后端；图片库、任务进度与失败记录等待真实接口接入。页面不使用模拟数据伪装搜索或入库成功。

## 已有接口约定

| 方法与路径 | 请求 |
| --- | --- |
| `POST /api/predict/embedding_file` | `multipart/form-data`，文件字段为 `file` |
| `POST /api/predict/embedding_url` | JSON：`{"img_url":"https://example.com/image.jpg"}` |

响应为 `{ "result": [...], "trace_id": "..." }`，也兼容 `result` 为包含 `results` 数组的对象。图片字段为 `image_url`，分数为 `similarity`（缺失时不显示虚构分数）；详情和缩略图来自 `details`。Global 搜索返回的候选结果不等同于已入库图片。

## 验证与部署

```bash
npm test
npm run test:e2e
npm run build
npm run preview
```

浏览器测试使用本机安装的 Google Chrome，自动启动或复用 `127.0.0.1:5173` 的开发服务。12 项交互测试覆盖 URL/文件搜索、缩略图回退、详情下载、筛选分页、错误与取消、导入队列、食材草稿以及 390px 窄屏布局。桌面与移动端截图输出到 `test-results/`，失败时保留 trace。测试中的网络响应使用受控模拟，不访问真实后端；实际页面请求真实后端。构建输出为 `dist/`，可部署到静态 Web 服务。

生产环境需要 Web 服务将 `/api` 反向代理到后端，或在构建前设置 `VITE_API_BASE_URL` 并配置后端 CORS。Vite 的 `server.proxy` 仅用于开发，`npm run preview` 和静态产物不会自动代理 `/api`。环境变量在构建时读取，部署后修改需重新构建；接口设置也可在浏览器内覆盖。
