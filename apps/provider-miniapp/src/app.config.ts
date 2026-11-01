import { nativeColors } from './styles/nativeColors';
export default defineAppConfig({
  pages: [
    'pages/login/index',
    'pages/register/index',
    'pages/orders/index',
    'pages/report/index',
    'pages/messages/index',
    'pages/message-detail/index',
    'pages/application-detail/index',
    'pages/reservations/index',
    'pages/wallet/index',
    'pages/withdrawAccounts/index',
    'pages/mine/index',
    'pages/evaluations/index',
    'pages/profile/index',
    'pages/skills/index',
    'pages/support/index',
    'pages/audition/index',
    'pages/points/index'
  ],
  window: {
    backgroundTextStyle: '@bgTxtStyle',
    navigationBarBackgroundColor: '@navBgColor',
    navigationBarTitleText: '兴安电竞陪玩端',
    navigationBarTextStyle: '@navTxtStyle',
    backgroundColor: '@bgColor'
  },
  darkmode: true,
  themeLocation: 'theme.json',
  tabBar: {
    custom: true,
    color: nativeColors.secondary,
    selectedColor: nativeColors.primary,
    backgroundColor: nativeColors.card,
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
