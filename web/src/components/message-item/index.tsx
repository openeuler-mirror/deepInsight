import { MessageType } from '@/constants/chat';
import { useSetModalState, useTranslate } from '@/hooks/common-hooks';
import { IReference, IReferenceChunk } from '@/interfaces/database/chat';
import classNames from 'classnames';
import { memo, useCallback, useEffect, useMemo, useState } from 'react';

import MessageItemChat from '@/components/message-item-chat';
import MessageItemDeepResearch from '@/components/message-item-deepresearch';
import {
  useFetchDocumentInfosByIds,
  useFetchDocumentThumbnailsByIds,
} from '@/hooks/document-hooks';
import { useDownloadFile } from '@/hooks/file-manager-hooks';
import { IRegenerateMessage, IRemoveMessageById } from '@/hooks/logic-hooks';
import { IMessage } from '@/pages/chat/interface';
import MarkdownContent from '@/pages/chat/markdown-content';
import { getExtension, isImage } from '@/utils/document-util';
import { DownloadOutlined, FilePptFilled } from '@ant-design/icons';
import {
  Avatar,
  Button,
  Divider,
  Flex,
  List,
  Space,
  Tooltip,
  Typography,
} from 'antd';
import FileIcon from '../file-icon';
import IndentedTreeModal from '../indented-tree/modal';
import NewDocumentLink from '../new-document-link';
import { useTheme } from '../theme-provider';
import { AssistantGroupButton, UserGroupButton } from './group-button';
import { useMessageFiles } from './hooks';
import styles from './index.less';

const { Text } = Typography;

interface IProps extends Partial<IRemoveMessageById>, IRegenerateMessage {
  item: IMessage;
  reference: IReference;
  loading?: boolean;
  sendLoading?: boolean;
  visibleAvatar?: boolean;
  nickname?: string;
  avatar?: string;
  avatarDialog?: string | null;
  clickDocumentButton?: (documentId: string, chunk: IReferenceChunk) => void;
  handlePlanButtonClick?: (str: string) => void;
  index: number;
  showLikeButton?: boolean;
  showLoudspeaker?: boolean;
  type?: string;
}

const MessageItem = ({
  item,
  reference,
  loading = false,
  avatar,
  avatarDialog,
  sendLoading = false,
  clickDocumentButton,
  handlePlanButtonClick,
  index,
  removeMessageById,
  regenerateMessage,
  showLikeButton = true,
  showLoudspeaker = true,
  visibleAvatar = true,
  type,
}: IProps) => {
  const { theme } = useTheme();
  const { t } = useTranslate('message');
  const isAssistant = item.role === MessageType.Assistant;
  const isUser = item.role === MessageType.User;
  const { data: documentList, setDocumentIds } = useFetchDocumentInfosByIds();
  const { data: documentThumbnails, setDocumentIds: setIds } =
    useFetchDocumentThumbnailsByIds();
  const { visible, hideModal, showModal } = useSetModalState();
  const [clickedDocumentId, setClickedDocumentId] = useState('');

  const referenceDocumentList = useMemo(() => {
    return reference?.doc_aggs ?? [];
  }, [reference?.doc_aggs]);

  const handleUserDocumentClick = useCallback(
    (id: string) => () => {
      setClickedDocumentId(id);
      showModal();
    },
    [showModal],
  );

  const { messageFiles, addMessageFiles } = useMessageFiles(
    (item as IMessage)?.files ?? [],
  );

  const all_content =
    item.content instanceof Array
      ? item.content.map((str) => JSON.stringify(str.content)).join('\n')
      : item.content;

  const handleRegenerateMessage = useCallback(() => {
    regenerateMessage?.(item);
  }, [regenerateMessage, item]);

  const { downloadFile, loading: downloadFileLoading } = useDownloadFile();
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

  useEffect(() => {
    const ids = item?.doc_ids ?? [];
    if (ids.length) {
      setDocumentIds(ids);
      const documentIds = ids.filter((x) => !(x in documentThumbnails));
      if (documentIds.length) {
        setIds(documentIds);
      }
    }
  }, [item.doc_ids, setDocumentIds, setIds, documentThumbnails]);

  function getTime(item) {
    if (item.updated_at) {
      return (item.updated_at - item.created_at).toFixed(3);
    }
    return 0;
  }

  return (
    <div
      className={classNames(styles.messageItem, {
        [styles.messageItemLeft]: item.role === MessageType.Assistant,
        [styles.messageItemRight]: item.role === MessageType.User,
      })}
    >
      <section
        className={classNames(styles.messageItemSection, {
          [styles.messageItemSectionLeft]: item.role === MessageType.Assistant,
          [styles.messageItemSectionRight]: item.role === MessageType.User,
        })}
      >
        <div
          className={classNames(styles.messageItemContent, {
            [styles.messageItemContentReverse]: item.role === MessageType.User,
          })}
        >
          {visibleAvatar &&
            (item.role === MessageType.User ? null : avatarDialog ? (
              <Avatar size={40} src={avatarDialog} />
            ) : (
              // <Avatar size={40} src={require("@/assets/reboot.png")} style={{ objectFit: 'cover' }} />
              <img
                src={require('@/assets/reboot.png')}
                style={{ height: 36, width: 36 }}
              />
            ))}

          <Flex vertical gap={8} flex={1}>
            <div
              className={
                isAssistant
                  ? theme === 'dark'
                    ? styles.messageTextDark
                    : styles.messageText
                  : styles.messageUserText
              }
            >
              {!(typeof item?.content === 'string') && type === 'research'  && (
                <MessageItemDeepResearch
                  content={item?.content}
                  handlePlanButtonClick={handlePlanButtonClick}
                  sendLoading={sendLoading}
                ></MessageItemDeepResearch>
              )}

              {!(typeof item?.content === 'string') && type === 'chat' && (
                <div>
                  <MessageItemChat
                    content={item?.content}
                    handlePlanButtonClick={handlePlanButtonClick}
                    sendLoading={sendLoading}
                  ></MessageItemChat>
                  <Divider />
                  {item?.content[1] && (
                    <MarkdownContent
                      loading={loading}
                      content={item?.content[1].content}
                      reference={reference}
                      clickDocumentButton={clickDocumentButton}
                    ></MarkdownContent>
                  )}
                </div>
              )}

              {typeof item?.content === 'string' && (
                <MarkdownContent
                  loading={loading}
                  content={item.content}
                  reference={reference}
                  clickDocumentButton={clickDocumentButton}
                ></MarkdownContent>
              )}
            </div>

            {isAssistant && index > 0 && (
              <div>
                <strong>回答总耗时：{getTime(item)}秒</strong>
              </div>
            )}

            {type === 'research' && isAssistant && index === 0 && (
              <div>
                <div className={styles.container}>
                  <div
                    onClick={handlePlanButtonClick('分析一下 OpenAI Sora')}
                    className={styles.fist_button}
                  >
                    分析一下 OpenAI Sora
                  </div>
                  <div
                    onClick={handlePlanButtonClick('分析一下Google A2A 协议')}
                    className={styles.fist_button}
                  >
                    分析一下Google A2A 协议
                  </div>
                </div>
              </div>
            )}

            {isAssistant && messageFiles.length > 0 && (
              <div>
                <strong>{t('generatedFiles')}</strong>
              </div>
            )}

            {isAssistant &&
              messageFiles.length > 0 &&
              messageFiles.map((file, index) => {
                return (
                  <Tooltip
                    title={t('download', { keyPrefix: 'common' })}
                    key={index}
                  >
                    <Button
                      type="primary"
                      ghost
                      onClick={() =>
                        onDownloadClick({
                          id: file.id,
                          filename: file.name,
                        })
                      }
                      style={{
                        display: 'flex',
                        alignItems: 'center',
                        gap: 8,
                      }}
                    >
                      <span style={{ display: 'flex', alignItems: 'center' }}>
                        <FilePptFilled />
                        <span style={{ marginLeft: 8 }}>{file.name}</span>
                      </span>
                      <DownloadOutlined />
                    </Button>
                  </Tooltip>
                );
              })}

            {isAssistant && referenceDocumentList.length > 0 && (
              <div>
                <strong>{t('referenceFrom')}</strong>
              </div>
            )}
            {isAssistant && referenceDocumentList.length > 0 && (
              <List
                bordered
                dataSource={referenceDocumentList}
                renderItem={(item) => {
                  return (
                    <List.Item>
                      <Flex gap={'small'} align="center">
                        <FileIcon
                          id={item.doc_id}
                          name={item.doc_name}
                        ></FileIcon>

                        <NewDocumentLink
                          documentId={item.doc_id}
                          documentName={item.doc_name}
                          prefix="document"
                          link={item.url}
                        >
                          {item.doc_name}
                        </NewDocumentLink>
                      </Flex>
                    </List.Item>
                  );
                }}
              />
            )}
            {isUser && documentList.length > 0 && (
              <List
                bordered
                dataSource={documentList}
                renderItem={(item) => {
                  // TODO:
                  // const fileThumbnail =
                  //   documentThumbnails[item.id] || documentThumbnails[item.id];
                  const fileExtension = getExtension(item.name);
                  return (
                    <List.Item>
                      <Flex gap={'small'} align="center">
                        <FileIcon id={item.id} name={item.name}></FileIcon>

                        {isImage(fileExtension) ? (
                          <NewDocumentLink
                            documentId={item.id}
                            documentName={item.name}
                            prefix="document"
                          >
                            {item.name}
                          </NewDocumentLink>
                        ) : (
                          <Button
                            type={'text'}
                            onClick={handleUserDocumentClick(item.id)}
                          >
                            <Text
                              style={{ maxWidth: '40vw' }}
                              ellipsis={{ tooltip: item.name }}
                            >
                              {item.name}
                            </Text>
                          </Button>
                        )}
                      </Flex>
                    </List.Item>
                  );
                }}
              />
            )}

            <Space>
              {isAssistant ? (
                index !== 0 && (
                  <AssistantGroupButton
                    messageId={item.id}
                    content={all_content}
                    prompt={item.prompt}
                    showLikeButton={showLikeButton}
                    audioBinary={item.audio_binary}
                    showLoudspeaker={showLoudspeaker}
                    message={item}
                    canGeneratePpt={type === 'research'}
                    addMessageFiles={addMessageFiles}
                  ></AssistantGroupButton>
                )
              ) : (
                <UserGroupButton
                  content={all_content}
                  messageId={item.id}
                  removeMessageById={removeMessageById}
                  regenerateMessage={
                    regenerateMessage && handleRegenerateMessage
                  }
                  sendLoading={sendLoading}
                ></UserGroupButton>
              )}

              {/* <b>{isAssistant ? '' : nickname}</b> */}
            </Space>
          </Flex>
        </div>
      </section>
      {visible && (
        <IndentedTreeModal
          visible={visible}
          hideModal={hideModal}
          documentId={clickedDocumentId}
        ></IndentedTreeModal>
      )}
    </div>
  );
};

export default memo(MessageItem);
