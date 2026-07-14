import { Product } from '@/types/product';

export const mockProducts: Product[] = [
  {
    id: 1,
    name: '三角洲行动 - 战术护航',
    description: '专业大神带你畅玩三角洲行动，烽火基地/危险行动全模式覆盖，全程语音指挥，畅快游戏体验',
    price: 80,
    originalPrice: 120,
    images: [
      'https://picsum.photos/id/1/400/400',
      'https://picsum.photos/id/2/400/400'
    ],
    category: '三角洲',
    tags: ['热门', '金牌陪玩'],
    sales: 856,
    stock: 50,
    specs: [
      { name: '时长', options: ['1小时', '2小时', '3小时', '5小时'] },
      { name: '模式', options: ['烽火基地', '危险行动', '全面封锁'] }
    ]
  },
  {
    id: 2,
    name: '暗区突围 - 武装护航',
    description: '资深玩家带队闯暗区，装备保险+撤离保障，让你的撤离率提升90%',
    price: 100,
    originalPrice: 150,
    images: [
      'https://picsum.photos/id/3/400/400',
      'https://picsum.photos/id/6/400/400'
    ],
    category: '暗区突围',
    tags: ['高胜率', '保值服务'],
    sales: 1243,
    stock: 30,
    specs: [
      { name: '时长', options: ['1小时', '2小时', '3小时'] },
      { name: '难度', options: ['普通', '困难', '伪装', '封锁'] }
    ]
  },
  {
    id: 3,
    name: '无畏契约 - 英雄教学',
    description: '国服/国际服皆可，专业教练指导，英雄技能教学+战术配合，让你快速上分',
    price: 60,
    originalPrice: 90,
    images: [
      'https://picsum.photos/id/8/400/400',
      'https://picsum.photos/id/9/400/400'
    ],
    category: '无畏契约',
    tags: ['教学指导', '上分神器'],
    sales: 2156,
    stock: 80,
    specs: [
      { name: '时长', options: ['1小时', '2小时', '3小时'] },
      { name: '模式', options: ['竞技排位', '团队死斗', '靶场训练'] }
    ]
  },
  {
    id: 4,
    name: 'CSGO - 完美搭档',
    description: '完美平台px2000分/2500分老兵组队，麦穗/AK/大地球段位，带你carry全场',
    price: 70,
    originalPrice: 100,
    images: [
      'https://picsum.photos/id/119/400/400',
      'https://picsum.photos/id/160/400/400'
    ],
    category: 'CSGO',
    tags: ['高段位', '五杀保证'],
    sales: 1879,
    stock: 40,
    specs: [
      { name: '时长', options: ['1小时', '2小时', '3小时', '5小时'] },
      { name: '模式', options: ['官匹竞技', '完美平台', '5E平台'] }
    ]
  },
  {
    id: 5,
    name: '和平精英 - 甜蜜护航',
    description: '王牌战神带你上分，4排满编车队，指挥流畅+枪法精准，场均10杀+',
    price: 90,
    originalPrice: 130,
    images: [
      'https://picsum.photos/id/201/400/400',
      'https://picsum.photos/id/64/400/400'
    ],
    category: '和平精英',
    tags: ['战神局', '满编车队'],
    sales: 3421,
    stock: 60,
    specs: [
      { name: '时长', options: ['1小时', '2小时', '3小时'] },
      { name: '模式', options: ['经典四排', '山谷猎狐', '火力全开'] }
    ]
  },
  {
    id: 6,
    name: '王者荣耀 - 王者带飞',
    description: '国服选手/职业退役，野王/法王/射王全位置覆盖，双排/三排/五排灵活组排，荣耀王者不是梦',
    price: 50,
    originalPrice: 80,
    images: [
      'https://picsum.photos/id/91/400/400',
      'https://picsum.photos/id/177/400/400'
    ],
    category: '王者荣耀',
    tags: ['国服选手', '快速上分'],
    sales: 4521,
    stock: 100,
    specs: [
      { name: '段位', options: ['钻石→星耀', '星耀→王者', '王者→荣耀', '百星王者'] },
      { name: '模式', options: ['双排', '三排', '五排'] }
    ]
  },
  {
    id: 7,
    name: '无畏契约 - 妹子上分',
    description: '专为小姐姐定制，女神陪玩+温柔教学+全程聊天陪伴，拒绝社恐，开心游戏',
    price: 65,
    originalPrice: 100,
    images: [
      'https://picsum.photos/id/338/400/400',
      'https://picsum.photos/id/1027/400/400'
    ],
    category: '无畏契约',
    tags: ['女神陪玩', '声甜温柔'],
    sales: 986,
    stock: 50,
    specs: [
      { name: '时长', options: ['1小时', '2小时', '3小时'] },
      { name: '服务', options: ['纯陪聊', '教学指导', '上分冲刺'] }
    ]
  },
  {
    id: 8,
    name: '和平精英 - 妹子上分',
    description: '王牌女神带队，钢枪不怂+语音陪聊+超级苟分，让菜鸟也能轻松上分',
    price: 75,
    originalPrice: 110,
    images: [
      'https://picsum.photos/id/64/400/400',
      'https://picsum.photos/id/91/400/400'
    ],
    category: '和平精英',
    tags: ['女神带队', '轻松上分'],
    sales: 1532,
    stock: 45,
    specs: [
      { name: '时长', options: ['1小时', '2小时', '3小时'] },
      { name: '模式', options: ['经典四排', '创意工坊'] }
    ]
  }
];

export const categories = ['全部', '三角洲', '暗区突围', '无畏契约', 'CSGO', '和平精英', '王者荣耀'];

export const getProductById = (id: number): Product | undefined => {
  return mockProducts.find(p => p.id === id);
};

export const getProductsByCategory = (category: string): Product[] => {
  if (category === '全部') return mockProducts;
  return mockProducts.filter(p => p.category === category);
};
