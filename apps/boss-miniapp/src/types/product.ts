export type ServiceType = 'play' | 'rank' | 'escort';

export interface Product {
  id: number;
  name: string;
  description: string;
  price: number;
  originalPrice: number;
  images: string[];
  category: string;
  tags: string[];
  serviceTypes: ServiceType[];
  sales: number;
  stock: number;
  specs: Spec[];
}

export interface Spec {
  name: string;
  options: string[];
}

export interface CartItem {
  product: Product;
  selectedSpecs: Record<string, string>;
  quantity: number;
}

export interface Order {
  id: number;
  orderNo: string;
  items: CartItem[];
  totalAmount: number;
  status: 'pending' | 'paid' | 'shipped' | 'completed' | 'cancelled';
  createTime: string;
}

export interface Player {
  id: number;
  nickname: string;
  avatar: string;
  gender: 'male' | 'female';
  games: string[];
  rank: string;
  rating: number;
  orderCount: number;
  onlineStatus: 'online' | 'busy' | 'offline';
  responseTime: string;
  priceOffset: number;
  winRate?: number;
  basePrice?: number;
  priceUnit?: string;
  voiceTags: string[];
  skillTags: string[];
  intro: string;
}
