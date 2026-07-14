import { useCallback, useRef, useState } from 'react';
import LoginSheet from '@/components/LoginSheet';
import { getStoredToken, LoginUser } from '@/utils/auth';

type SuccessCallback = (user: LoginUser) => void;

/** 登录守卫：页面调用 ensureLogin(cb)，已登录直接执行，未登录弹出微信快捷登录窗，
 * 登录成功后自动执行暂存的回调。页面需将返回的 loginSheet 渲染进 JSX。
 */
export const useLoginGuard = () => {
  const [visible, setVisible] = useState(false);
  const pendingRef = useRef<SuccessCallback | null>(null);

  const ensureLogin = useCallback((onSuccess?: SuccessCallback): boolean => {
    if (getStoredToken()) {
      onSuccess?.(undefined as unknown as LoginUser);
      return true;
    }
    pendingRef.current = onSuccess || null;
    setVisible(true);
    return false;
  }, []);

  const handleClose = useCallback(() => {
    setVisible(false);
    pendingRef.current = null;
  }, []);

  const handleSuccess = useCallback((user: LoginUser) => {
    const cb = pendingRef.current;
    pendingRef.current = null;
    setVisible(false);
    cb?.(user);
  }, []);

  const loginSheet = (
    <LoginSheet visible={visible} onClose={handleClose} onSuccess={handleSuccess} />
  );

  return { ensureLogin, loginSheet };
};
