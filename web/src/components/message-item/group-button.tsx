import { PromptIcon } from '@/assets/icon/Icon';
import CopyToClipboard from '@/components/copy-to-clipboard';
import { useGetChatSearchParams } from '@/hooks/chat-hooks';
import { useSetModalState } from '@/hooks/common-hooks';
import { IRemoveMessageById, useGeneratePptSse } from '@/hooks/logic-hooks';
import { Message } from '@/interfaces/database/chat';
import { IFile } from '@/interfaces/database/file-manager';
import {
  DeleteOutlined,
  DislikeOutlined,
  FilePptFilled,
  LikeOutlined,
  PauseCircleOutlined,
  SoundOutlined,
  SyncOutlined,
} from '@ant-design/icons';
import { Button, Radio, Spin, Tooltip, message as messageAnt } from 'antd';
import { useCallback, useState } from 'react';
import FeedbackModal from './feedback-modal';
import { useRemoveMessage, useSendFeedback, useSpeech } from './hooks';
import PromptModal from './prompt-modal';

interface IProps {
  messageId: string;
  content: string;
  prompt?: string;
  showLikeButton: boolean;
  audioBinary?: string;
  showLoudspeaker?: boolean;
  message?: Message;
  canGeneratePpt: boolean;
  addMessageFiles?: (file: IFile) => void;
}

export const AssistantGroupButton = ({
  messageId,
  content,
  prompt,
  audioBinary,
  showLikeButton,
  showLoudspeaker = true,
  message,
  canGeneratePpt,
  addMessageFiles,
}: IProps) => {
  const { visible, hideModal, showModal, onFeedbackOk, loading } =
    useSendFeedback(messageId);
  const {
    visible: promptVisible,
    hideModal: hidePromptModal,
    showModal: showPromptModal,
  } = useSetModalState();
  const { handleRead, ref, isPlaying } = useSpeech(content, audioBinary);

  const handleLike = useCallback(() => {
    onFeedbackOk({ thumbup: true });
  }, [onFeedbackOk]);

  const [controller, setController] = useState(new AbortController());
  const [pptGenerating, setPptGenerating] = useState(false);
  const { conversationId } = useGetChatSearchParams();
  const { generatePptRequest, answer, done, stopOutputMessage } =
    useGeneratePptSse(undefined, addMessageFiles);

  const handlePptGenerateClick = async () => {
    if (pptGenerating) {
      stopOutputMessage();
      setController((pre) => {
        pre.abort();
        return new AbortController();
      });
      setPptGenerating(false);
      return;
    }
    setPptGenerating(true);
    try {
      const res = await generatePptRequest(
        {
          message: message,
          conversation_id: conversationId,
        },
        controller,
      );

      if (res && (res?.response.status !== 200 || res?.data.code !== 0)) {
        // cancel loading
        messageAnt.error(res?.data?.message);
      } else {
        messageAnt.success('PPT生成成功');
      }
    } catch (error) {
      messageAnt.error('PPT生成失败');
    } finally {
      setPptGenerating(false);
    }
  };

  return (
    <>
      <Radio.Group
        size="small"
        style={{ display: 'flex', alignItems: 'center', gap: 16 }}
      >
        <Radio.Button value="a">
          <CopyToClipboard text={content}></CopyToClipboard>
        </Radio.Button>
        {showLoudspeaker && (
          <Radio.Button value="b" onClick={handleRead}>
            <Tooltip title={'朗读内容'}>
              {isPlaying ? <PauseCircleOutlined /> : <SoundOutlined />}
            </Tooltip>
            <audio src="" ref={ref}></audio>
          </Radio.Button>
        )}
        {showLikeButton && (
          <>
            <Radio.Button value="c" onClick={handleLike}>
              <LikeOutlined />
            </Radio.Button>
            <Radio.Button value="d" onClick={showModal}>
              <DislikeOutlined />
            </Radio.Button>
          </>
        )}
        {prompt && (
          <Radio.Button value="e" onClick={showPromptModal}>
            <PromptIcon style={{ fontSize: '16px' }} />
          </Radio.Button>
        )}
        {canGeneratePpt && (
          <Spin spinning={pptGenerating}>
            <Button
              type="primary"
              ghost
              icon={<FilePptFilled />}
              loading={pptGenerating}
              onClick={handlePptGenerateClick}
            >
              {pptGenerating ? 'PPT生成中' : '生成PPT'}
            </Button>
          </Spin>
        )}
      </Radio.Group>
      {visible && (
        <FeedbackModal
          visible={visible}
          hideModal={hideModal}
          onOk={onFeedbackOk}
          loading={loading}
        ></FeedbackModal>
      )}
      {promptVisible && (
        <PromptModal
          visible={promptVisible}
          hideModal={hidePromptModal}
          prompt={prompt}
        ></PromptModal>
      )}
    </>
  );
};

interface UserGroupButtonProps extends Partial<IRemoveMessageById> {
  messageId: string;
  content: string;
  regenerateMessage?: () => void;
  sendLoading: boolean;
}

export const UserGroupButton = ({
  content,
  messageId,
  sendLoading,
  removeMessageById,
  regenerateMessage,
}: UserGroupButtonProps) => {
  const { onRemoveMessage, loading } = useRemoveMessage(
    messageId,
    removeMessageById,
  );
  return (
    <Radio.Group size="small">
      <Radio.Button value="a">
        <CopyToClipboard text={content}></CopyToClipboard>
      </Radio.Button>
      {regenerateMessage && (
        <Radio.Button
          value="b"
          onClick={regenerateMessage}
          disabled={sendLoading}
        >
          <Tooltip title={'重新生成'}>
            <SyncOutlined spin={sendLoading} />
          </Tooltip>
        </Radio.Button>
      )}
      {removeMessageById && (
        <Radio.Button value="c" onClick={onRemoveMessage} disabled={loading}>
          <Tooltip title={'删除'}>
            <DeleteOutlined spin={loading} />
          </Tooltip>
        </Radio.Button>
      )}
    </Radio.Group>
  );
};
