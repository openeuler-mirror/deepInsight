import { ChatSearchParams, MessageType, SourcesType } from '@/constants/chat';
import { fileIconMap } from '@/constants/common';
import {
  useFetchManualConversation,
  useFetchManualDialog,
  useFetchNextConversation,
  useFetchNextConversationList,
  useFetchNextDialog,
  useFetchNextDialogList,
  useGetChatSearchParams,
  useRemoveNextConversation,
  useRemoveNextDialog,
  useSetNextDialog,
  useUpdateNextConversation,
} from '@/hooks/chat-hooks';
import {
  useSetModalState,
  useShowDeleteConfirm,
  useTranslate,
} from '@/hooks/common-hooks';
import {
  useRegenerateMessage,
  useSelectDerivedMessages,
  useSendMessageWithSse,
} from '@/hooks/logic-hooks';
import {
  IConversation,
  IDialog,
  Message,
  MessageContent,
} from '@/interfaces/database/chat';
import { getFileExtension } from '@/utils';
import api from '@/utils/api';
import { getConversationId } from '@/utils/chat';
import { useMutationState } from '@tanstack/react-query';
import { message as antdMessage } from 'antd';
import { get } from 'lodash';
import trim from 'lodash/trim';
import {
  ChangeEventHandler,
  useCallback,
  useEffect,
  useMemo,
  useState,
} from 'react';
import { useSearchParams } from 'umi';
import { v4 as uuid } from 'uuid';
import {
  IClientConversation,
  IMessage,
  VariableTableDataType,
} from './interface';

export const useSetChatRouteParams = () => {
  const [currentQueryParameters, setSearchParams] = useSearchParams();
  const newQueryParameters: URLSearchParams = useMemo(
    () => new URLSearchParams(currentQueryParameters.toString()),
    [currentQueryParameters],
  );

  const setConversationIsNew = useCallback(
    (value: string) => {
      newQueryParameters.set(ChatSearchParams.isNew, value);
      setSearchParams(newQueryParameters);
    },
    [newQueryParameters, setSearchParams],
  );

  const getConversationIsNew = useCallback(() => {
    return newQueryParameters.get(ChatSearchParams.isNew);
  }, [newQueryParameters]);

  return { setConversationIsNew, getConversationIsNew };
};

export const useSetNewConversationRouteParams = () => {
  const [currentQueryParameters, setSearchParams] = useSearchParams();
  const newQueryParameters: URLSearchParams = useMemo(
    () => new URLSearchParams(currentQueryParameters.toString()),
    [currentQueryParameters],
  );

  const setNewConversationRouteParams = useCallback(
    (conversationId: string, isNew: string) => {
      newQueryParameters.set(ChatSearchParams.ConversationId, conversationId);
      newQueryParameters.set(ChatSearchParams.isNew, isNew);
      setSearchParams(newQueryParameters);
    },
    [newQueryParameters, setSearchParams],
  );

  return { setNewConversationRouteParams };
};

export const useSelectCurrentDialog = () => {
  const data = useMutationState({
    filters: { mutationKey: ['fetchDialog'] },
    select: (mutation) => {
      return get(mutation, 'state.data.data', {});
    },
  });

  return (data.at(-1) ?? {}) as IDialog;
};

export const useSelectPromptConfigParameters = (): VariableTableDataType[] => {
  const { data: currentDialog } = useFetchNextDialog();

  const finalParameters: VariableTableDataType[] = useMemo(() => {
    const parameters = currentDialog?.prompt_config?.parameters ?? [];
    if (!currentDialog.id) {
      // The newly created chat has a default parameter
      return [{ key: uuid(), variable: 'knowledge', optional: false }];
    }
    return parameters.map((x) => ({
      key: uuid(),
      variable: x.key,
      optional: x.optional,
    }));
  }, [currentDialog]);

  return finalParameters;
};

export const useDeleteDialog = () => {
  const showDeleteConfirm = useShowDeleteConfirm();

  const { removeDialog } = useRemoveNextDialog();

  const onRemoveDialog = (dialogIds: Array<string>) => {
    showDeleteConfirm({ onOk: () => removeDialog(dialogIds) });
  };

  return { onRemoveDialog };
};

export const useHandleItemHover = () => {
  const [activated, setActivated] = useState<string>('');
  const [onClick, setOnClick] = useState<string>('');

  const handleItemEnter = (id: string) => {
    setActivated(id);
  };

  const handleItemLeave = () => {
    setActivated('');
  };

  const handleItemClick = () => {
    console.log('handleItemClick');
    setOnClick(true);
  };

  return {
    activated,
    handleItemEnter,
    handleItemLeave,
    handleItemClick,
  };
};

export const useEditDialog = () => {
  const [dialog, setDialog] = useState<IDialog>({} as IDialog);
  const { fetchDialog } = useFetchManualDialog();
  const { setDialog: submitDialog, loading } = useSetNextDialog();

  const {
    visible: dialogEditVisible,
    hideModal: hideDialogEditModal,
    showModal: showDialogEditModal,
  } = useSetModalState();

  const hideModal = useCallback(() => {
    setDialog({} as IDialog);
    hideDialogEditModal();
  }, [hideDialogEditModal]);

  const onDialogEditOk = useCallback(
    async (dialog: IDialog) => {
      const ret = await submitDialog(dialog);

      if (ret === 0) {
        hideModal();
      }
    },
    [submitDialog, hideModal],
  );

  const handleShowDialogEditModal = useCallback(
    async (dialogId?: string) => {
      if (dialogId) {
        const ret = await fetchDialog(dialogId);
        if (ret.code === 0) {
          setDialog(ret.data);
        }
      }
      showDialogEditModal();
    },
    [showDialogEditModal, fetchDialog],
  );

  const clearDialog = useCallback(() => {
    setDialog({} as IDialog);
  }, []);

  return {
    dialogSettingLoading: loading,
    initialDialog: dialog,
    onDialogEditOk,
    dialogEditVisible,
    hideDialogEditModal: hideModal,
    showDialogEditModal: handleShowDialogEditModal,
    clearDialog,
  };
};

//#region conversation

const useFindPrologueFromDialogList = () => {
  const { dialogId } = useGetChatSearchParams();
  const { data: dialogList } = useFetchNextDialogList(true);
  const prologue = useMemo(() => {
    return dialogList.find((x) => x.id === dialogId)?.prompt_config.prologue;
  }, [dialogId, dialogList]);

  return prologue;
};

export const useSelectDerivedConversationList = () => {
  const { t } = useTranslate('chat');

  const [list, setList] = useState<Array<IConversation>>([]);
  const { data: conversationList, loading } = useFetchNextConversationList();
  const { dialogId } = useGetChatSearchParams();
  const { setNewConversationRouteParams } = useSetNewConversationRouteParams();
  const prologue = useFindPrologueFromDialogList();

  const addTemporaryConversation = useCallback(() => {
    const conversationId = getConversationId();
    setList((pre) => {
      if (dialogId) {
        setNewConversationRouteParams(conversationId, 'true');
        const nextList = [
          {
            id: conversationId,
            name: '新会话',
            dialog_id: dialogId,
            is_new: true,
            message: [
              {
                content: prologue,
                role: MessageType.Assistant,
              },
            ],
          } as any,
          ...conversationList,
        ];
        return nextList;
      }

      return pre;
    });
  }, [conversationList, dialogId, prologue, t, setNewConversationRouteParams]);

  // When you first enter the page, select the top conversation card

  useEffect(() => {
    setList([...conversationList]);
  }, [conversationList]);

  return { list, addTemporaryConversation, loading };
};

export const useSetConversation = () => {
  const { dialogId } = useGetChatSearchParams();
  const { updateConversation } = useUpdateNextConversation();

  const setConversation = useCallback(
    async (
      message: MessageContent,
      isNew: boolean = false,
      conversationId?: string,
    ) => {
      const data = await updateConversation({
        dialog_id: dialogId,
        name: message,
        is_new: isNew,
        conversation_id: conversationId,
        message: [
          {
            role: MessageType.Assistant,
            content: message,
          },
        ],
      });

      return data;
    },
    [updateConversation, dialogId],
  );

  return { setConversation };
};

export const useSelectNextMessages = () => {
  const {
    ref,
    setDerivedMessages,
    derivedMessages,
    addNewestAnswer,
    addNewestAnswerDeduplicateByMessageId,
    addNewestQuestion,
    removeLatestMessage,
    removeMessageById,
    removeMessagesAfterCurrentMessage,
  } = useSelectDerivedMessages();
  const { data: conversation, loading } = useFetchNextConversation();
  const { conversationId, dialogId, isNew } = useGetChatSearchParams();
  const prologue = useFindPrologueFromDialogList();

  const addPrologue = useCallback(() => {
    if (dialogId !== '' && isNew === 'true') {
      const nextMessage = {
        role: MessageType.Assistant,
        content: prologue,
        id: uuid(),
      } as IMessage;

      setDerivedMessages([nextMessage]);
    }
  }, [dialogId, isNew, prologue, setDerivedMessages]);

  useEffect(() => {
    addPrologue();
  }, [addPrologue]);

  useEffect(() => {
    if (
      conversationId &&
      isNew !== 'true' &&
      conversation.message?.length > 0
    ) {
      setDerivedMessages(conversation.message);
    }

    if (!conversationId) {
      setDerivedMessages([]);
    }
  }, [conversation.message, conversationId, setDerivedMessages, isNew]);

  return {
    ref,
    derivedMessages,
    loading,
    addNewestAnswer,
    addNewestAnswerDeduplicateByMessageId,
    addNewestQuestion,
    removeLatestMessage,
    removeMessageById,
    removeMessagesAfterCurrentMessage,
  };
};

export const useHandleMessageInputChange = () => {
  const [value, setValue] = useState('');

  const handleInputChange: ChangeEventHandler<HTMLTextAreaElement> = (e) => {
    const value = e.target.value;
    // const nextValue = value.replaceAll('\\n', '\n').replaceAll('\\t', '\t');
    setValue(value);
  };

  return {
    handleInputChange,
    value,
    setValue,
  };
};

function getKeyName(key: string) {
  return 'sources_' + key;
}

function getSources() {
  let sources = [];
  if (localStorage.getItem(getKeyName(SourcesType.knowledge)) === 'true') {
    sources.push(SourcesType.knowledge);
  }
  if (localStorage.getItem(getKeyName(SourcesType.web_search)) === 'true') {
    sources.push(SourcesType.web_search);
  }
  if (localStorage.getItem(getKeyName(SourcesType.intra_search)) === 'true') {
    sources.push(SourcesType.intra_search);
  }
  return sources;
}

function getMode() {
  let mode = 'normal';
  if (
    localStorage.getItem(getKeyName(SourcesType.deepinsight_mode)) === 'true'
  ) {
    mode = 'expert';
  }
  return mode;
}

export const useSendNextMessage = (controller: AbortController) => {
  const { setConversation } = useSetConversation();
  const { conversationId, isNew } = useGetChatSearchParams();
  const { handleInputChange, value, setValue } = useHandleMessageInputChange();

  const [isDeepresearch, setDeepresearch] = useState<boolean>(false);

  const { updateConversation } = useUpdateNextConversation();

  const switchDeepinsight = useCallback(
    async (enable: boolean) => {
      if (!isNew) {
        const ret = await updateConversation({
          conversation_id: conversationId,
          type: enable ? 'deepinsight' : 'chat',
          is_new: false,
        });

        if (ret.code === 0) {
          setDeepresearch(enable);
        }
      } else {
        setDeepresearch(enable);
      }
    },
    [updateConversation, conversationId, setDeepresearch],
  );

  // 默认请求问一问接口
  const { send, answer, done } = useSendMessageWithSse(
    api.questionConversation,
  );

  const {
    ref,
    derivedMessages,
    loading,
    addNewestAnswer,
    addNewestAnswerDeduplicateByMessageId,
    addNewestQuestion,
    removeLatestMessage,
    removeMessageById,
    removeMessagesAfterCurrentMessage,
  } = useSelectNextMessages();
  const { setConversationIsNew, getConversationIsNew } =
    useSetChatRouteParams();

  const stopOutputMessage = useCallback(() => {
    controller.abort();
  }, [controller]);

  // 请求接口 /completion
  const sendMessage = useCallback(
    async ({
      message,
      currentConversationId,
      messages,
    }: {
      message: Message;
      currentConversationId?: string;
      messages?: Message[];
    }) => {
      const res = await send(
        {
          conversation_id: currentConversationId ?? conversationId,
          messages: [...(messages ?? derivedMessages ?? []), message],
          type: isDeepresearch ? 'deepresearch' : 'chat',
          sources: getSources(),
          deepinsight_mode: getMode(),
        },
        controller,
        addNewestAnswerDeduplicateByMessageId,
      );

      if (res && (res?.response.status !== 200 || res?.data?.code !== 0)) {
        // cancel loading
        setValue(typeof message.content === 'string' ? message.content : '');
        antdMessage.error(res?.data?.message);
        removeLatestMessage();
      }
    },
    [
      derivedMessages,
      conversationId,
      removeLatestMessage,
      setValue,
      send,
      controller,
      isDeepresearch,
    ],
  );

  const handleSendMessage = useCallback(
    async (message: Message) => {
      const isNew = getConversationIsNew();
      if (isNew !== 'true') {
        sendMessage({ message });
      } else {
        const data = await setConversation(
          message.content,
          true,
          conversationId,
        );
        if (data.code === 0) {
          setConversationIsNew('');
          const id = data.data.id;
          // currentConversationIdRef.current = id;
          sendMessage({
            message,
            currentConversationId: id,
            messages: data.data.message,
          });
        }
      }
    },
    [
      setConversation,
      sendMessage,
      setConversationIsNew,
      getConversationIsNew,
      conversationId,
    ],
  );

  const { regenerateMessage } = useRegenerateMessage({
    removeMessagesAfterCurrentMessage,
    sendMessage,
    messages: derivedMessages,
  });

  // useEffect(() => {
  //   //  #1289
  //   if (answer.answer && conversationId && isNew !== 'true') {
  //     addNewestAnswer(answer);
  //   }
  // }, [answer, addNewestAnswer, conversationId, isNew]);

  useEffect(() => {
    //  #1289
    if (answer.messages && conversationId && isNew !== 'true') {
      addNewestAnswerDeduplicateByMessageId(answer);
    }
  }, [answer, addNewestAnswerDeduplicateByMessageId, conversationId, isNew]);

  const handlePressEnter = useCallback(
    (documentIds: string[]) => {
      if (trim(value) === '') return;
      const id = uuid();
      addNewestQuestion({
        content: value,
        doc_ids: documentIds,
        id,
        role: MessageType.User,
      });
      if (done) {
        setValue('');
        handleSendMessage({
          id,
          content: value.trim(),
          role: MessageType.User,
          doc_ids: documentIds,
        });
      }
    },
    [addNewestQuestion, handleSendMessage, done, setValue, value],
  );

  return {
    handlePressEnter,
    handleInputChange,
    value,
    setValue,
    regenerateMessage,
    sendLoading: !done,
    loading,
    ref,
    derivedMessages,
    removeMessageById,
    stopOutputMessage,
    isDeepresearch,
    switchDeepinsight,
    setDeepresearch,
  };
};

export const useGetFileIcon = () => {
  const getFileIcon = (filename: string) => {
    const ext: string = getFileExtension(filename);
    const iconPath = fileIconMap[ext as keyof typeof fileIconMap];
    return `@/assets/svg/file-icon/${iconPath}`;
  };

  return getFileIcon;
};

export const useDeleteConversation = () => {
  const showDeleteConfirm = useShowDeleteConfirm();
  const { removeConversation } = useRemoveNextConversation();

  const deleteConversation = (conversationIds: Array<string>) => async () => {
    const ret = await removeConversation(conversationIds);

    return ret;
  };

  const onRemoveConversation = (conversationIds: Array<string>) => {
    showDeleteConfirm({ onOk: deleteConversation(conversationIds) });
  };

  return { onRemoveConversation };
};

export const useRenameConversation = () => {
  const [conversation, setConversation] = useState<IClientConversation>(
    {} as IClientConversation,
  );
  const { fetchConversation } = useFetchManualConversation();
  const {
    visible: conversationRenameVisible,
    hideModal: hideConversationRenameModal,
    showModal: showConversationRenameModal,
  } = useSetModalState();
  const { updateConversation, loading } = useUpdateNextConversation();

  const onConversationRenameOk = useCallback(
    async (name: string) => {
      const ret = await updateConversation({
        conversation_id: conversation.id,
        name,
        is_new: false,
      });

      if (ret.code === 0) {
        hideConversationRenameModal();
      }
    },
    [updateConversation, conversation, hideConversationRenameModal],
  );

  const handleShowConversationRenameModal = useCallback(
    async (conversationId: string) => {
      const ret = await fetchConversation(conversationId);
      if (ret.code === 0) {
        setConversation(ret.data);
      }
      showConversationRenameModal();
    },
    [showConversationRenameModal, fetchConversation],
  );

  return {
    conversationRenameLoading: loading,
    initialConversationName: conversation.name,
    onConversationRenameOk,
    conversationRenameVisible,
    hideConversationRenameModal,
    showConversationRenameModal: handleShowConversationRenameModal,
  };
};

export const useGetSendButtonDisabled = () => {
  const { dialogId, conversationId } = useGetChatSearchParams();

  return dialogId === '' || conversationId === '';
};

export const useSendButtonDisabled = (value: string) => {
  return trim(value) === '';
};

export const useCreateConversationBeforeUploadDocument = () => {
  const { setConversation } = useSetConversation();
  const { dialogId } = useGetChatSearchParams();
  const { getConversationIsNew } = useSetChatRouteParams();

  const createConversationBeforeUploadDocument = useCallback(
    async (message: string) => {
      const isNew = getConversationIsNew();
      if (isNew === 'true') {
        const data = await setConversation(message, true);

        return data;
      }
    },
    [setConversation, getConversationIsNew],
  );

  return {
    createConversationBeforeUploadDocument,
    dialogId,
  };
};
//#endregion
