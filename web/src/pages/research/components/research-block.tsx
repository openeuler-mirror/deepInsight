// Copyright (c) 2025 Bytedance Ltd. and/or its affiliates
// SPDX-License-Identifier: MIT
import {
  ScrollContainer,
  type ScrollContainerRef,
} from '@/components/deer-flow/scroll-container';
import { Card } from '@/components/ui/card';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { useGetChatSearchParams } from '@/hooks/chat-hooks';
import { cn } from '@/lib/utils';
import { ResearchActivitiesBlock } from '@/pages/research/components/research-activities-block';
import { VerticalAlignBottomOutlined } from '@ant-design/icons';
import { FloatButton, Tooltip } from 'antd';
import { useEffect, useRef, useState } from 'react';
import { ResearchReportBlock } from './research-report-block';

export function ResearchBlock({
  className,
  researchId = null,
  controller = null,
  derivedMessages = null,
  sendLoading = false,
}: {
  className?: string;
  researchId: string | null;
  controller: AbortController;
  derivedMessages: [];
  sendLoading: boolean;
}) {
  const [activeTab, setActiveTab] = useState('activities');
  const [userTab, setUserTab] = useState(false);
  const hasReport = true;
  const { conversationId, isNew } = useGetChatSearchParams();
  const scrollContainerRef = useRef<ScrollContainerRef>(null);

  useEffect(() => {
    if (!userTab) {
      // 思考完，默认跳到报告
      if (!sendLoading || isReport()) {
        setActiveTab('report');
      } else {
        setActiveTab('activities');
      }
    }
    if (!sendLoading) {
      setUserTab(false);
      setActiveTab('report');
    }
  }, [sendLoading, derivedMessages]);

  function isReport() {
    // console.log('判断-----', derivedMessages);
    if (
      derivedMessages &&
      Array.isArray(derivedMessages[derivedMessages.length - 1].content)
    ) {
      let arr = derivedMessages[derivedMessages.length - 1].content;
      for (let i = 0; i < arr.length; i++) {
        // console.log(arr[i])
        if (
          typeof arr[i].content === 'string' &&
          arr[i].content === '正在生成报告'
        ) {
          return true;
        }
      }
    }
    return false;
  }

  async function downloadReport() {
    window.open(
      '/v1/deepinsight/download_pdf?conversation_id=' + conversationId,
    );
  }

  // console.log('ResearchBlock ===derivedMessages===', derivedMessages);

  function isDownload() {
    let type =
      derivedMessages[derivedMessages.length - 1]?.content[
        derivedMessages[derivedMessages.length - 1]?.content.length - 1
      ]?.type;
    return type === 'report';
  }

  function updateUserTab(tab) {
    setActiveTab(tab);
    setUserTab(true);
  }

  return (
    <div
      className={cn('h-full w-full', className)}
      style={{ background: '#f5f5f5' }}
    >
      <Card
        className={cn('relative h-full w-full', className)}
        style={{ background: '#f5f5f5' }}
      >
        <Tabs
          className="flex h-full w-full flex-col"
          value={activeTab}
          onValueChange={(value) => updateUserTab(value)}
        >
          <div className="flex w-full justify-center">
            <TabsList className="">
              <TabsTrigger
                className="px-8"
                value="report"
                disabled={!hasReport}
              >
                报告
              </TabsTrigger>
              <TabsTrigger className="px-8" value="activities">
                思考过程
              </TabsTrigger>
            </TabsList>
          </div>
          <TabsContent
            className="h-full min-h-0 flex-grow px-8"
            value="report"
            forceMount
            hidden={activeTab !== 'report'}
          >
            <ScrollContainer
              className="px-5pb-20 h-full"
              scrollShadowColor="var(--card)"
              autoScrollToBottom={false}
              ref={scrollContainerRef}
            >
              {isDownload() ? (
                <Tooltip title={'报告下载'}>
                  <FloatButton
                    style={{ top: 80 }}
                    onClick={() => downloadReport()}
                    icon={<VerticalAlignBottomOutlined />}
                  >
                    下载
                  </FloatButton>
                </Tooltip>
              ) : null}
              <ResearchReportBlock
                className="mt-4"
                controller={controller}
                derivedMessages={derivedMessages}
                loading={sendLoading}
              />
            </ScrollContainer>
          </TabsContent>
          <TabsContent
            className="h-full min-h-0 flex-grow px-8"
            value="activities"
            forceMount
            hidden={activeTab !== 'activities'}
          >
            <ScrollContainer
              className="h-full"
              scrollShadowColor="var(--card)"
              autoScrollToBottom={true}
              ref={scrollContainerRef}
            >
              {researchId && (
                <ResearchActivitiesBlock
                  className="mt-4"
                  controller={controller}
                  derivedMessages={derivedMessages}
                  loading={sendLoading}
                />
              )}
            </ScrollContainer>
          </TabsContent>
        </Tabs>
      </Card>
    </div>
  );
}
