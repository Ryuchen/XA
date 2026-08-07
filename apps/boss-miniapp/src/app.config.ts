export default defineAppConfig({
  pages: [
    'pages/home/index',
    'pages/companions/index',
    'pages/selfOrder/index',
    'pages/login/index',
    'pages/category/index',
    'pages/chat/index',
    'pages/mine/index',
    'pages/productDetail/index',
    'pages/serviceCard/index',
    'pages/orderList/index',
    'pages/checkout/index',
    'pages/messageDetail/index',
    'pages/players/index',
    'pages/customer/wallet/index',
    'pages/customer/checkin/index',
    'pages/announcement/index',
    'pages/announcementDetail/index',
    'pages/coupon/index',
    'pages/coupon/mine/index',
    'pages/favorite/index',
    'pages/settings/index',
    'pages/audition/index'
  ],
  window: {
    backgroundTextStyle: '@bgTxtStyle',
    navigationBarBackgroundColor: '@navBgColor',
    navigationBarTitleText: '游戏陪玩',
    navigationBarTextStyle: '@navTxtStyle',
    backgroundColor: '@bgColor'
  },
  darkmode: true,
  themeLocation: 'theme.json',
  tabBar: {
    custom: false,
    color: '#999999',
    selectedColor: '#6C5CE7',
    backgroundColor: '#FFFFFF',
    borderStyle: 'black',
    list: [
      {
        pagePath: 'pages/home/index',
        text: '首页',
        iconPath: 'assets/tabbar/home_normal.png',
        selectedIconPath: 'assets/tabbar/home_active.png'
      },
      {
        pagePath: 'pages/companions/index',
        text: '陪玩',
        iconPath: 'assets/tabbar/companions_normal.png',
        selectedIconPath: 'assets/tabbar/companions_active.png'
      },
      {
        pagePath: 'pages/category/index',
        text: '服务',
        iconPath: 'assets/tabbar/service_normal.png',
        selectedIconPath: 'assets/tabbar/service_active.png'
      },
      {
        pagePath: 'pages/chat/index',
        text: '消息',
        iconPath: 'assets/tabbar/message_normal.png',
        selectedIconPath: 'assets/tabbar/message_active.png'
      },
      {
        pagePath: 'pages/mine/index',
        text: '我的',
        iconPath: 'assets/tabbar/mine_normal.png',
        selectedIconPath: 'assets/tabbar/mine_active.png'
      }
    ]
  }
})
