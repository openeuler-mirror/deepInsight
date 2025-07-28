// Copyright (c) 2025 Bytedance Ltd. and/or its affiliates
// SPDX-License-Identifier: MIT

import { memo, useCallback, useEffect, useState } from 'react';

import { cn } from '@/lib/utils';

import { Tooltip } from './tooltip';

function Image({
  className,
  imageClassName,
  imageTransition,
  src,
  alt,
  fallback = null,
}: {
  className?: string;
  imageClassName?: string;
  imageTransition?: boolean;
  src: string;
  alt: string;
  fallback?: React.ReactNode;
}) {
  const [isLoading, setIsLoading] = useState(true);
  const [isError, setIsError] = useState(false);

  useEffect(() => {
    setIsError(false);
    setIsLoading(true);

    if (!src) return;

    setTimeout(() => setIsLoading(true), 300);
  }, [src]);

  const handleLoad = useCallback(() => {
    setIsError(false);
    setIsLoading(false);
  }, []);
  const handleError = useCallback(
    (e: React.SyntheticEvent<HTMLImageElement>) => {
      console.log(e);
      e.currentTarget.style.display = 'none';
      console.warn(`Markdown: Image "${e.currentTarget.src}" failed to load`);
      setIsError(true);
    },
    [],
  );
  return (
    <span
      className={cn(
        'block w-fit overflow-hidden',
        className,
        'grid place-items-center',
      )}
    >
      {isError ? (
        <div className="image-error">🚨 图片加载失败</div>
      ) : (
        <Tooltip title={alt ?? 'No caption'}>
          <img
            className={cn(
              'size-full object-cover',
              imageTransition && 'transition-all duration-200 ease-out',
              imageClassName,
            )}
            src={src}
            alt={alt}
            onLoad={handleLoad}
            onError={handleError}
            referrerpolicy={'no-referrer'}
            style={{ maxWidth: '80%', display: 'inline' }}
          />
        </Tooltip>
      )}
    </span>
  );
}

export default memo(Image);
