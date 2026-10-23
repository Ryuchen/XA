import { useLaunch, useDidShow, useDidHide } from '@tarojs/taro';
import { getStoredToken } from '@/utils/auth';
import { wsService } from '@/services/websocket';
import { useEffect } from 'react';
import { orderAlerts } from '@/services/orderAlerts';
import './app.scss';
import { useH5Controls } from '@/hooks/useH5Controls';

function App(props) {
  useH5Controls();
  useEffect(() => () => orderAlerts.dispose(), []);
  const connectIfLoggedIn = () => {
    if (getStoredToken()) {
      wsService.connect();
    }
  };

  useLaunch(() => {
    orderAlerts.show();
    connectIfLoggedIn();
  });

  useDidShow(() => {
    orderAlerts.show();
    connectIfLoggedIn();
  });

  useDidHide(() => {
    orderAlerts.hide();
    wsService.disconnect();
  });

  return props.children;
}

export default App;
