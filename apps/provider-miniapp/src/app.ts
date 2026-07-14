import { useLaunch, useDidShow, useDidHide } from '@tarojs/taro';
import { getStoredToken } from '@/utils/auth';
import { wsService } from '@/services/websocket';
import './app.scss';

function App(props) {
  const connectIfLoggedIn = () => {
    if (getStoredToken()) {
      wsService.connect();
    }
  };

  useLaunch(() => {
    connectIfLoggedIn();
  });

  useDidShow(() => {
    connectIfLoggedIn();
  });

  useDidHide(() => {
    wsService.disconnect();
  });

  return props.children;
}

export default App;
