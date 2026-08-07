import { useDidShow, useDidHide } from '@tarojs/taro';
import { wsService } from './services/websocket';
// 顶层引入以确保 store 在首个请求发出前完成求值，
// 从而注册好 401 的登出钩子（见 store/user.ts 末尾）。
import { useUserStore } from './store';
import './app.scss';

function App(props) {
  useDidShow(() => {
    if (useUserStore.getState().token) {
      wsService.connect();
    }
  });

  useDidHide(() => {
    wsService.disconnect();
  });

  return props.children;
}

export default App;
