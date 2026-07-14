export default defineAppConfig({
  pages: [
    'pages/login/index',
    'pages/orders/index',
    'pages/report/index',
    'pages/messages/index',
    'pages/message-detail/index',
    'pages/wallet/index',
    'pages/mine/index',
    'pages/evaluations/index',
    'pages/profile/index',
    'pages/audition/index'
  ],
  window: {
    backgroundTextStyle: 'light',
    navigationBarBackgroundColor: '#fff',
    navigationBarTitleText: '兴安电竞陪玩端',
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
        pagePath: 'pages/orders/index',
        text: '接单'
      },
      {
        pagePath: 'pages/report/index',
        text: '报单'
      },
      {
        pagePath: 'pages/messages/index',
        text: '消息'
      },
      {
        pagePath: 'pages/wallet/index',
        text: '钱包'
      },
      {
        pagePath: 'pages/mine/index',
        text: '我的'
      }
    ]
  }
})
