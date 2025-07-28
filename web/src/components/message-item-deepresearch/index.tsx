import { useTranslate } from '@/hooks/common-hooks';
import { useDownloadFile } from '@/hooks/file-manager-hooks';
import {
  DeepResearchMessagePart,
  DeepResearchMessagePartToolCall,
  IReference,
} from '@/interfaces/database/chat';
import MarkdownContent from '@/pages/chat/markdown-content';
import {
  AlignLeftOutlined,
  CaretRightOutlined,
  FileDoneOutlined,
  GlobalOutlined,
  HistoryOutlined,
} from '@ant-design/icons';
import { Button, Card, Flex, Timeline } from 'antd';
import classNames from 'classnames';
import { memo, useState } from 'react';
import DocumentChunkView from '../document_chunk_view';
import styles from './index.less';

const MessageItemDeepResearch = ({
  content,
  handlePlanButtonClick,
  sendLoading,
}: {
  content: DeepResearchMessagePart[];
  handlePlanButtonClick?: (str: string) => void;
  sendLoading: boolean;
}) => {
  const { t } = useTranslate('message');
  const [expanded, setExpanded] = useState(true);

  const { downloadFile, loading } = useDownloadFile();

  const onDownloadClick = ({
    id,
    filename,
  }: {
    id: string;
    filename: string;
  }) => {
    downloadFile({
      id: id,
      filename: filename,
    });
  };

  content
      .filter((messagePart, index) => messagePart.type === 'title');

  let htmlItem = '';

  return (
    <Card className={classNames(styles.researchCard)}>
      <section>
        <div
          className={classNames(styles.header)}
          onClick={() => setExpanded(!expanded)}
        >
          {/*思考过程->执行计划*/}
          <span className={classNames(styles.title)}>{t('executePlan')}</span>
          <CaretRightOutlined
            className={classNames(styles.collapseIcon, {
              [styles.expanded]: expanded,
            })}
          />
        </div>

        {expanded && (
          <Card className={classNames(styles.thinkContent)}>
            {content
              .filter((messagePart, index) => messagePart.type === 'title')
              .map((messagePart, index) => {
                // console.log('messagePart======', messagePart);
                if (messagePart.type === 'tool_calls') {
                  let toolCall =
                    messagePart.content as DeepResearchMessagePartToolCall;
                  if (toolCall.tool_name === 'retrieve') {
                    return (
                      <Flex
                        key={index}
                        className={classNames(styles.executeTools)}
                      >
                        {/*<strong>{t('executeTools') + ': '}</strong>*/}
                        {/*{toolCall.tool_name}*/}
                        {toolCall.result instanceof Array ? (
                          <DocumentChunkView documentChunks={toolCall.result} />
                        ) : (
                          <div>{toolCall.result}</div>
                        )}
                      </Flex>
                    );
                  }
                } else {



                  htmlItem += messagePart?.content + '\n\r';
                }
              })}
            {/*执行计划时间轴*/}
            <div>
              <Timeline
                items={[
                  {
                    children: (
                      <MarkdownContent
                        loading={false}
                        content={htmlItem as string}
                        reference={{} as IReference}
                      ></MarkdownContent>
                    ),
                    dot: <GlobalOutlined />,
                    color: 'blank',
                  },
                  {
                    children: (
                      <span style={{ fontSize: '16px' }}>检索分析</span>
                    ),
                    dot: <AlignLeftOutlined />,
                    color: 'blank',
                  },
                  {
                    children: (
                      <span style={{ fontSize: '16px' }}>输出报告</span>
                    ),
                    dot: <FileDoneOutlined />,
                    color: 'blank',
                  },
                ]}
              />
              <div>
                <HistoryOutlined style={{ paddingRight: 13 }} />
                <span style={{ fontSize: '16px' }}>预计3-5分钟生成完成</span>
              </div>
            </div>
          </Card>
        )}
        <div
          style={{
            width: '100%',
            display: 'flex',
            justifyContent: 'flex-end', // 右对齐
            // marginRight: '50px',
          }}
        >
          <Button
            onClick={handlePlanButtonClick('修改计划')}
            style={{
              marginTop: '20px',
              marginRight: '16px',
              borderRadius: '20px',
              fontSize: '16px',
            }}
            disabled={sendLoading}
          >
            修改计划
          </Button>
          <Button
            onClick={handlePlanButtonClick('开始研究')}
            style={{
              marginTop: '20px',
              borderRadius: '20px',
              fontSize: '16px',
            }}
            type="primary"
            disabled={sendLoading}
          >
            开始研究
          </Button>
        </div>
      </section>


    </Card>
  );
};

export default memo(MessageItemDeepResearch);
