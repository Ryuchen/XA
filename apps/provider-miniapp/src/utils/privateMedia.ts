import Taro from '@tarojs/taro';
import { request, ApiResponse } from './request';

export async function renewPrivateImage(url: string): Promise<string> {
  const ticket = url.match(/\/api\/media\/private\/([^/?]+)\/?$/)?.[1];
  if (!ticket) return url;
  const response = await request<ApiResponse<{ url: string }>>('/media/renew/', 'POST', { ticket });
  if (response.code !== 0 || !response.data?.url) throw new Error(response.msg || '图片刷新失败');
  return response.data.url;
}

export async function previewPrivateImages(current: string, urls: string[]) {
  try {
    const renewed = await Promise.all(urls.map(renewPrivateImage));
    await Taro.previewImage({ current: renewed[Math.max(0, urls.indexOf(current))], urls: renewed });
  } catch (error) {
    Taro.showToast({ title: (error as Error).message || '图片加载失败，请重试', icon: 'none' });
  }
}
