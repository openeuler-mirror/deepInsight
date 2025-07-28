import MessageItem from '@/components/message-item';
import { MessageType } from '@/constants/chat';
import { Flex, Spin, Splitter } from 'antd';
import {
  useCreateConversationBeforeUploadDocument,
  useGetFileIcon,
  useGetSendButtonDisabled,
  useSelectDerivedConversationList,
  useSendButtonDisabled,
  useSendNextMessage,
} from '../hook';
import { addMessageItemFiles, buildMessageItemReference } from '../utils';

import { ScrollContainer } from '@/components/deer-flow/scroll-container';
import MessageInput from '@/components/message-input';
import PdfDrawer from '@/components/pdf-drawer';
import { useClickDrawer } from '@/components/pdf-drawer/hooks';
import {
  useFetchNextConversation,
  useGetChatSearchParams,
} from '@/hooks/chat-hooks';
import { ResearchBlock } from '@/pages/research/components/research-block';
import { buildMessageUuidWithRole } from '@/utils/chat';
import { memo, useCallback, useEffect, useState } from 'react';
import styles from './index.less';

interface IProps {
  controllercontroller: AbortController;
  assistant: object;
}

const ChatContainer = ({ controller, assistant }: IProps) => {
  const { conversationId, isNew } = useGetChatSearchParams();
  const { data: conversation } = useFetchNextConversation();
  const {
    value,
    ref,
    loading,
    sendLoading,
    derivedMessages,
    handleInputChange,
    handlePressEnter,
    regenerateMessage,
    removeMessageById,
    stopOutputMessage,
    isDeepResearch,
    switchDeepinsight,
    setDeepResearch,
    setValue,
  } = useSendNextMessage(controller);

  const { visible, hideModal, documentId, selectedChunk, clickDocumentButton } =
    useClickDrawer();
  const disabled = useGetSendButtonDisabled();
  const sendDisabled = useSendButtonDisabled(value);
  useGetFileIcon();
  // const { data: userInfo } = useFetchUserInfo();
  const { createConversationBeforeUploadDocument } =
    useCreateConversationBeforeUploadDocument();
  const [planButtonClick, setPlanButtonClick] = useState<string>('');
  const handlePlanButtonClick = (str: string) => () => {
    setValue(str);
    setPlanButtonClick(str + Date.now());
  };
  const { addTemporaryConversation } = useSelectDerivedConversationList();

  const handleCreateTemporaryConversation = useCallback(() => {
    addTemporaryConversation();
    setTimeout(() => {
      setPlanButtonClick(value);
    }, 600);
  }, [addTemporaryConversation, handlePressEnter, planButtonClick]);

  const handleNewConversation = () => {
    let type =
      derivedMessages[derivedMessages.length - 1]?.content[
        derivedMessages[derivedMessages.length - 1]?.content.length - 1
      ]?.type;

    if (type === 'result') {
      handleCreateTemporaryConversation();
    } else {
      handlePressEnter();
    }
  };

  useEffect(() => {
    // value改变触发回车
    if (value) {
      handlePressEnter();
    }
  }, [planButtonClick]);

  useEffect(() => {
    setDeepResearch(conversation?.type === 'deepresearch');
  }, [conversation?.type]);

  return (
    <>
      <Splitter style={{ boxShadow: '0 0 10px rgba(0, 0, 0, 0.1)' }}>
        <Splitter.Panel defaultSize="40%" min="20%" max="70%">
          <Flex flex={1} className={styles.chatContainer} vertical>
            {/*深度研究输入左边*/}
            <ScrollContainer
              className="h-4/5"
              scrollShadowColor="var(--card)"
              autoScrollToBottom={true}
            >
              <Flex flex={1} vertical className={styles.messageContainer}>
                <div>
                  <Spin spinning={loading}>
                    {derivedMessages?.map((message, i) => {
                      if (message && message?.content === '') {
                        return;
                      }
                      return (
                        <MessageItem
                          loading={
                            message.role === MessageType.Assistant &&
                            sendLoading &&
                            derivedMessages.length - 1 === i
                          }
                          key={buildMessageUuidWithRole(message)}
                          item={addMessageItemFiles(
                            {
                              files: conversation.files,
                            },
                            message,
                          )}
                          // nickname={userInfo.nickname}
                          // avatar={userInfo.avatar}
                          avatarDialog={conversation.avatar}
                          reference={buildMessageItemReference(
                            {
                              message: derivedMessages,
                              reference: conversation.reference,
                            },
                            message,
                          )}
                          clickDocumentButton={clickDocumentButton}
                          index={i}
                          removeMessageById={removeMessageById}
                          // regenerateMessage={regenerateMessage}
                          sendLoading={sendLoading}
                          handlePlanButtonClick={handlePlanButtonClick}
                          type={'research'}
                        ></MessageItem>
                      );
                    })}
                  </Spin>
                </div>
                <div ref={ref} />
              </Flex>
            </ScrollContainer>
            <MessageInput
              disabled={disabled}
              sendDisabled={sendDisabled}
              sendLoading={sendLoading}
              value={value}
              onInputChange={handleInputChange}
              onPressEnter={handleNewConversation}
              conversationId={conversationId}
              createConversationBeforeUploadDocument={
                createConversationBeforeUploadDocument
              }
              stopOutputMessage={stopOutputMessage}
              isDeepinsight={isDeepResearch}
              switchDeepinsight={switchDeepinsight}
              assistant={assistant}
              type={'research'}
            ></MessageInput>
          </Flex>
          <PdfDrawer
            visible={visible}
            hideModal={hideModal}
            documentId={documentId}
            chunk={selectedChunk}
          ></PdfDrawer>
        </Splitter.Panel>
        <Splitter.Panel>
          {/*搜索研究右边*/}
          <ResearchBlock
            researchId={'111'}
            controller={controller}
            derivedMessages={derivedMessages}
            sendLoading={sendLoading}
          />
        </Splitter.Panel>
      </Splitter>
    </>
  );
};

export default memo(ChatContainer);
