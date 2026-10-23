import { ApiResponse, request } from '@/utils/request';
import Taro from '@tarojs/taro';

export interface SupportContactCard {
  id: number;
  name: string;
  company: string;
  wechat_id: string;
  wecom_corp_id: string;
  wecom_service_url: string;
  avatar_url: string;
  qrcode_url: string;
  tips: string;
  customer_reference?: string;
  order_no?: string;
}

export const fetchContactCard = (context: CustomerServiceContext = {}) => {
  const query = context.orderId ? `?order_id=${context.orderId}` : context.cardId ? `?card_id=${context.cardId}` : '';
  return request<ApiResponse<SupportContactCard | null>>(`/support/contact-card/${query}`, 'GET');
};

export const fetchContactCards = () => {
  return request<ApiResponse<SupportContactCard[]>>('/support/contact-cards/', 'GET');
};

export interface CustomerServiceContext {
  title?: string;
  path?: string;
  cardId?: number;
  orderId?: number;
}

export interface CustomerServiceOpenResult {
  opened: boolean;
  reason?: 'unsupported' | 'unconfigured' | 'failed';
  card?: SupportContactCard | null;
}

/** 使用微信原生能力打开企业微信客服；H5 或未配置时由调用方进入名片兜底页。 */
export const openWecomCustomerService = async (
  context: CustomerServiceContext = {},
): Promise<CustomerServiceOpenResult> => {
  let response: ApiResponse<SupportContactCard | null>;
  try { response = await fetchContactCard(context); }
  catch { return { opened: false, reason: 'failed' }; }
  const card = response.code === 0 ? response.data || null : null;
  if (process.env.TARO_ENV !== 'weapp') {
    return { opened: false, reason: 'unsupported', card };
  }
  if (!card?.wecom_corp_id || !card.wecom_service_url) {
    return { opened: false, reason: 'unconfigured', card };
  }

  try {
    const path = (context.path || '/pages/home/index').replace(/^\//, '');
    await Taro.openCustomerServiceChat({
      corpId: card.wecom_corp_id,
      extInfo: { url: card.wecom_service_url },
      showMessageCard: true,
      sendMessageTitle: [card.customer_reference, context.title || '兴安电竞客服咨询'].filter(Boolean).join(' · ').slice(0, 100),
      sendMessagePath: path,
    });
    return { opened: true, card };
  } catch {
    return { opened: false, reason: 'failed', card };
  }
};
