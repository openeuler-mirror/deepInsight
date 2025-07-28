import { useAuth } from '@/hooks/auth-hooks';
import { Outlet } from 'umi';

export default () => {
  const { isLogin } = useAuth();
  if (isLogin === true) {
    return <Outlet />;
  } else if (isLogin === false) {
    return <Outlet />;
  }

  return <></>;
};
