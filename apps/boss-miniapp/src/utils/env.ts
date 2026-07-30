export const BASE_URL = process.env.TARO_APP_API || 'http://127.0.0.1:8000/api';

// 媒体资源域名：优先独立配置，否则由 API 基址去掉 /api 后缀推导。
// 后端返回绝对 URL 时前端直接使用；返回相对路径时用此基址补全。
export const MEDIA_BASE = process.env.TARO_APP_MEDIA || BASE_URL.replace(/\/api\/?$/, '');
