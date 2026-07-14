import { useEffect } from 'react';
import { useDidShow, useDidHide } from '@tarojs/taro';
import { getStoredToken } from './utils/auth';
import { wsService } from './services/websocket';
import './app.scss';

function App(props) {
  useEffect(() => {});

  useDidShow(() => {
    if (getStoredToken()) {
      wsService.connect();
    }
  });

  useDidHide(() => {
    wsService.disconnect();
  });

  return props.children;
}

export default App;
