import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { App, ConfigProvider, message, theme } from 'antd';
import dayjs from 'dayjs';
import advancedFormat from 'dayjs/plugin/advancedFormat';
import customParseFormat from 'dayjs/plugin/customParseFormat';
import localeData from 'dayjs/plugin/localeData';
import weekOfYear from 'dayjs/plugin/weekOfYear';
import weekYear from 'dayjs/plugin/weekYear';
import weekday from 'dayjs/plugin/weekday';
import React, { ReactNode } from 'react';
import { ThemeProvider, useTheme } from './components/theme-provider';
import { TooltipProvider } from './components/ui/tooltip';

dayjs.extend(customParseFormat);
dayjs.extend(advancedFormat);
dayjs.extend(weekday);
dayjs.extend(localeData);
dayjs.extend(weekOfYear);
dayjs.extend(weekYear);

const queryClient = new QueryClient();

function Root({ children }: React.PropsWithChildren) {
  const { theme: themeragflow } = useTheme();
  // 统一设置消息停留时间
  message.config({
    duration: 0.5, // 单位：秒
  });

  return (
    <>
      <ConfigProvider
        theme={{
          token: {
            fontFamily: 'Inter',
          },
          algorithm:
            themeragflow === 'dark'
              ? theme.darkAlgorithm
              : theme.defaultAlgorithm,
        }}
      >
        <App>{children}</App>
      </ConfigProvider>
    </>
  );
}

const RootProvider = ({ children }: React.PropsWithChildren) => {
  return (
    <TooltipProvider>
      <QueryClientProvider client={queryClient}>
        <ThemeProvider defaultTheme="light" storageKey="ragflow-ui-theme">
          <Root>{children}</Root>
        </ThemeProvider>
      </QueryClientProvider>
    </TooltipProvider>
  );
};
export function rootContainer(container: ReactNode) {
  return <RootProvider>{container}</RootProvider>;
}
