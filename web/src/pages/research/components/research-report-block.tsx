// Copyright (c) 2025 Bytedance Ltd. and/or its affiliates
// SPDX-License-Identifier: MIT

import { useFetchNextConversation } from '@/hooks/chat-hooks';
import { IReference } from '@/interfaces/database/chat';
import { cn } from '@/lib/utils';
import MarkdownContent from '@/pages/chat/markdown-content';
import { Spin } from 'antd';
import { useEffect, useRef, useState } from 'react';

export function ResearchReportBlock({
  className,
  messageId,
  controller = null,
  derivedMessages = null,
  loading = false,
}: {
  className?: string;
  researchId: string;
  messageId: string;
  controller: AbortController;
  derivedMessages: [];
  loading: boolean;
}) {


  // 报告过程往下滚动，用户往上滑动就停止自动往下滚动
  const scrollContainerRef = useRef<HTMLDivElement>(null);
  const [lastScrollTop, setLastScrollTop] = useState(0);
  const [lastUser, setLastUser] = useState(true);
  useEffect(() => {
    let type =
      derivedMessages[derivedMessages.length - 1]?.content[
        derivedMessages[derivedMessages.length - 1]?.content.length - 1
      ]?.type;
    if (type != 'report') {
      setLastUser(true);
      setLastScrollTop(0);
      return;
    }
    if (scrollContainerRef.current && lastUser) {
      const currentScrollTop = scrollContainerRef.current.scrollTop;
      if (currentScrollTop < lastScrollTop) {
        setLastUser(false);
        return;
      } else if (currentScrollTop > lastScrollTop) {
      }
      setLastScrollTop(currentScrollTop);
      scrollContainerRef.current.scrollTop =
        scrollContainerRef.current.scrollHeight;
    }
  }, [derivedMessages]);

  if (derivedMessages.length <= 0) {
    return null;
  }
  let myMd =
    derivedMessages[derivedMessages.length - 1]?.content[
      derivedMessages[derivedMessages.length - 1]?.content.length - 1
    ]?.content;

  let type =
    derivedMessages[derivedMessages.length - 1]?.content[
      derivedMessages[derivedMessages.length - 1]?.content.length - 1
    ]?.type;

  if (typeof myMd != 'string') {
    return;
  }
  if (type != 'report') {
    return (
      <span>
        <Spin style={{ padding: 10 }} />
        生成中...
      </span>
    );
  }
  return (
    <div
      ref={scrollContainerRef}
      style={{
        height: loading ? '800px' : '',
        overflowY: loading ? 'auto' : 'none',
      }}
      className={cn('relative flex flex-col pt-4 pb-8', className)}
    >
      {/*<Markdown animate>{myMd}</Markdown>*/}
      <MarkdownContent
        loading={true}
        content={myMd as string}
        reference={{} as IReference}
      ></MarkdownContent>
    </div>
  );
}
