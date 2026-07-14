export interface Product {
  id: number;
  name: string;
  description: string;
  price: number;
  originalPrice: number;
  images: string[];
  category: string;
  tags: string[];
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
