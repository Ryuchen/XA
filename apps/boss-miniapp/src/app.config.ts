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
    'pages/customerService/index',
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
    backgroundTextStyle: 'light',
    navigationBarBackgroundColor: '#fff',
    navigationBarTitleText: '游戏陪玩',
    navigationBarTextStyle: 'black'
  },
  tabBar: {
    custom: true,
    color: '#999999',
    selectedColor: '#6C5CE7',
    backgroundColor: '#FFFFFF',
    borderStyle: 'black',
    list: [
      {
        pagePath: 'pages/home/index',
        text: '首页'
      },
      {
        pagePath: 'pages/companions/index',
        text: '陪玩'
      },
      {
        pagePath: 'pages/category/index',
        text: '服务'
      },
      {
        pagePath: 'pages/chat/index',
        text: '消息'
      },
      {
        pagePath: 'pages/mine/index',
        text: '我的'
      }
    ]
  }
})
