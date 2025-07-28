// Copyright (c) 2025 Bytedance Ltd. and/or its affiliates
// SPDX-License-Identifier: MIT

import { IReference } from '@/interfaces/database/chat';
import { cn, getRangeStr } from '@/lib/utils';
import MarkdownContent from '@/pages/chat/markdown-content';
import { GlobalOutlined, SearchOutlined } from '@ant-design/icons';
import { Avatar, Card, Spin, Timeline, Tooltip } from 'antd';
import { useEffect, useRef, useState } from 'react';
import styled from 'styled-components';

const { Meta } = Card;

const MyDiv = styled.div`
  .ant-timeline-item-head {
    background: none;
    margin-top: 2px;
  }
`;

const MyCard = styled.div`
  .ant-card-meta-description {
    display: -webkit-box;
    -webkit-line-clamp: 3; /* 限制显示行数 */
    -webkit-box-orient: vertical;
    overflow: hidden;
  }
  .ant-card .ant-card-meta-title {
    font-size: 15px;
  }
`;

export function ResearchActivitiesBlock({
  className,
  researchId,
  controller = null,
  derivedMessages = null,
  loading = false,
}: {
  className?: string;
  researchId: string;
  controller: AbortController;
  derivedMessages: [];
  loading: boolean;
}) {
  // 思考过程往下滚动，用户往上滑动就停止自动往下滚动
  const scrollContainerRef = useRef<HTMLDivElement>(null);
  const [lastScrollTop, setLastScrollTop] = useState(0);
  const [lastUser, setLastUser] = useState(true);

  useEffect(() => {
    let type =
      derivedMessages[derivedMessages.length - 1]?.content[
        derivedMessages[derivedMessages.length - 1]?.content.length - 1
      ]?.type;
    if (type === 'report') {
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

  if (typeof derivedMessages[derivedMessages.length - 1].content === 'string') {
    return;
  }

  function getListCard(data) {
    data = data.slice(0, 3);
    let htmlArr = [];
    data.map((item) => {
      htmlArr.push(
        <Tooltip title={getContent(item)}>
          <a href={item.url} target={'_blank'}>
            <MyCard>
              <Card style={{ width: 260, background: '#f2f3f3' }}>
                <Meta
                  avatar={
                    <Avatar
                      src={item.favicon ? item.favicon : '/icon-internet.svg'}
                    />
                  }
                  title={item.title ? item.title : getContent(item)}
                  description={item.content}
                />
              </Card>
            </MyCard>
          </a>
        </Tooltip>,
      );
    });
    return (
      <div
        style={{
          display: 'flex',
          flexWrap: 'wrap',
          gap: '20px',
          marginTop: 20,
        }}
      >
        {htmlArr}
      </div>
    );
  }

  function getKnowledgeCard(data) {
    data = data.slice(0, 3);
    let htmlArr = [];
    data.map((item) => {
      htmlArr.push(
        <Tooltip title={getRangeStr(item.content_with_weight, 150)}>
          <MyCard>
            <Card>
              <Meta
                avatar={<Avatar src={'/icon-knowledgeLab.svg'} />}
                title={getRangeStr(item.content_with_weight, 22)}
                description={getRangeStr(item.content_with_weight, 150)}
              />
            </Card>
          </MyCard>
        </Tooltip>,
      );
    });
    return (
      <div style={{ display: 'flex', flexWrap: 'wrap', gap: '16px' }}>
        {htmlArr}
      </div>
    );
  }

  function getContent(item) {
    return getRangeStr(
      item.description && item.description.length > 0
        ? item.description
        : item.content,
      150,
    );
  }

  function getList() {
    let nodeList = [];

    // console.log(derivedMessages)

    let messagees = derivedMessages[derivedMessages.length - 1].content.filter(
      (messagePart, index) => messagePart.type != 'report',
    );

    // console.log('ResearchActivitiesBlock ===messagees===', messagees);

    let htmlArr = [];
    // 兼容两种模式：
    if (
      messagees[0].type.startsWith('title') ||
      messagees[1]?.type.startsWith('title')
    ) {
      for (let i = 0; i < messagees.length; i++) {
        let x = messagees[i];
        // 如果是时间轴
        if (x.type.startsWith('title')) {
          // console.log("------------setp")
          htmlArr = [];
          // console.log('时间轴----', x.content)
          htmlArr.push(
            <h1 style={{ fontWeigh: 700, color: '#1570ef' }}>{x.content}</h1>,
          );
          let dotHtml = (
            <SearchOutlined style={{ backgroundColor: '#edeef6' }} />
          );
          if (x.type.startsWith('step_tool')) {
            dotHtml = <GlobalOutlined style={{ background: '#edeef6' }} />;
          }
          nodeList.push({
            color: 'blank',
            dot: dotHtml,
            children: (
              <div
                style={{
                  background: '#fff',
                  padding: '20px 20px 30px 20px',
                  borderRadius: '30px',
                }}
              >
                {htmlArr}
              </div>
            ),
          });
        } else if (x.type.startsWith('content')) {
          // 如果是工具调用
          if (x.type.startsWith('tool_call')) {
            // 1.result是数组情况
            if (Array.isArray(x.content.result)) {
              // console.log('array------', x.content.result)
              if (!x.content.result.length) {
                htmlArr.push(getError(x));
              } else {
                let data = [];
                // 网络搜索工具
                x.content.result.map((item) => {
                  // console.log("item.results========",item.results)
                  if (item.results) {
                    // console.log(item.results)
                    if (Array.isArray(item.results)) {
                      item.results.map((item, index) =>
                        // htmlArr.push(getNode(item, index))
                        data.push(item),
                      );
                    } else if (
                      item.results &&
                      Array.isArray(item.results.results)
                    ) {
                      item.results.results.map((item, index) =>
                        // htmlArr.push(getNode(item, index)),
                        data.push(item),
                      );
                    }
                  }
                });
                htmlArr.push(getListCard(data));
              }
            } else {
              // console.log('obj------', x.content.result)
              if (!Array.isArray(x.content.result.results)) {
                htmlArr.push(getError(x));
              } else {
                x.content.result.results.map((item, index) => {
                  if (item) {
                    htmlArr.push(getNode(item, index));
                  }
                });
              }
            }
          } else {
            if (x.type.startsWith('content_plan')) {
              continue;
            }
            // console.log("------------content")
            // htmlArr.push(<Markdown>{x.content}</Markdown>)
            htmlArr.push(
              <MarkdownContent
                loading={true}
                content={x.content as string}
                reference={{} as IReference}
                type={'research'}
              ></MarkdownContent>,
            );
          }
        }
      }
    } else {
      // console.error("-------------------")
      messagees.map((x) => {
        let htmlArr = [];
        let dotHtml = <SearchOutlined />;
        // console.error(x)
        if (x.type === 'content') {
          htmlArr.push(
            <MarkdownContent
              loading={true}
              content={x.content as string}
              reference={{} as IReference}
              type={'research'}
            ></MarkdownContent>,
          );
        } else if (x.type === 'tool_calls') {
          dotHtml = <GlobalOutlined style={{ backgroundColor: '#edeef6' }} />;
          x.content.result.map((item) => {
            if (item.results && item.results.results) {
              item.results.results.map((item, index) =>
                htmlArr.push(
                  <Tooltip title={getContent(item)} key={index}>
                    <a
                      href={item.url}
                      target={'_blank'}
                      style={{
                        backgroundColor: '#cedffb',
                        padding: 6,
                        margin: 8,
                        borderRadius: 10,
                        display: 'inline-flex',
                      }}
                    >
                      {item.title.length > 20
                        ? item.title.substring(0, 17) + '...'
                        : item.title}
                    </a>
                  </Tooltip>,
                ),
              );
            }
          });
        }
        if (htmlArr.length > 0) {
          nodeList.push({
            color: 'green',
            dot: dotHtml,
            children: <div>{htmlArr}</div>,
          });
        }
      });
    }
    // console.log(nodeList)
    return nodeList;
  }

  function getError(x) {
    return (
      <Tooltip title={getRangeStr(x.content.result.error, 150)}>
        <span
          style={{
            backgroundColor: '#fcebeb',
            padding: 6,
            margin: 8,
            borderRadius: 10,
            display: 'inline-flex',
          }}
        >
          工具执行异常
        </span>
      </Tooltip>
    );
  }

  function getNode(item, index) {
    return (
      <Tooltip title={getContent(item)} key={index}>
        <a
          href={item.url}
          target={'_blank'}
          style={{
            backgroundColor: '#cedffb',
            padding: 6,
            margin: 8,
            borderRadius: 10,
            display: 'inline-flex',
          }}
        >
          {getRangeStr(item.title, 20)}
        </a>
      </Tooltip>
    );
  }

  //  知识库折叠
  function getKnowledgeNode(list) {
    let htmlItem = [];
    list.map((item) => {
      htmlItem.push(
        <Tooltip
          title={getRangeStr(item.content_with_weight, 150)}
          key={item.kb_id}
        >
          <a
            style={{
              backgroundColor: '#cedffb',
              padding: 6,
              margin: 8,
              borderRadius: 10,
              display: 'inline-flex',
            }}
          >
            {getRangeStr(item.content_with_weight, 20)}
          </a>
        </Tooltip>,
      );
    });

    return htmlItem;
  }

  return (
    <div className={cn(' flex flex-col pt-4 pb-8', className)}>
      <MyDiv
        ref={scrollContainerRef}
        style={{
          height: loading ? '800px' : '',
          overflowY: loading ? 'auto' : 'none',
        }}
      >
        <Timeline
          key={'timeLineId'}
          style={{ marginLeft: 6 }}
          // pending={loading ? '思考中...' : null}
          items={getList()}
          id={'timeLineId'}
        ></Timeline>
        {loading ? (
          <span>
            {' '}
            <Spin style={{ padding: 10 }} />
            思考中...{' '}
          </span>
        ) : null}
      </MyDiv>

      {/*{derivedMessages[derivedMessages.length - 1].content
        .filter(
          (messagePart, index) =>
            messagePart.process === 'think',
        )
        .map((x) => {
            return <Timeline
              items={getItems(x)}
            />;
        })}*/}
    </div>
  );
}