import { ReactComponent as ChatAppCube } from '@/assets/svg/chat-app-cube.svg';
import RenameModal from '@/components/rename-modal';
import { DeleteOutlined, EditOutlined } from '@ant-design/icons';
import {
  Button,
  Card,
  Divider,
  Dropdown,
  Flex,
  MenuProps,
  Space,
  Spin,
  Tooltip,
  Typography,
} from 'antd';
import { MenuItemProps } from 'antd/lib/menu/MenuItem';
import classNames from 'classnames';
import { useCallback, useEffect, useState } from 'react';
import ChatContainer from './chat-container';
import {
  useDeleteConversation,
  useEditDialog,
  useHandleItemHover,
  useRenameConversation,
  useSelectDerivedConversationList,
} from './hook';

import EmbedModal from '@/components/api-service/embed-modal';
import { useShowEmbedModal } from '@/components/api-service/hooks';
import { useTheme } from '@/components/theme-provider';
import { SharedFrom } from '@/constants/chat';
import {
  useClickConversationCard,
  useClickDialogCard,
  useFetchResearchDialog,
  useGetChatSearchParams,
} from '@/hooks/chat-hooks';
import { useTranslate } from '@/hooks/common-hooks';
import { useSetSelectedRecord } from '@/hooks/logic-hooks';
import { IDialog } from '@/interfaces/database/chat';
import styles from './index.less';

const { Text } = Typography;

const Chat = () => {
  // 深度研究助理
  const { data: dialog, loading: dialogLoading } = useFetchResearchDialog();
  const { handleClickDialog } = useClickDialogCard();
  const { dialogId: dialogIdSearchParams } = useGetChatSearchParams();
  const { onRemoveConversation } = useDeleteConversation();
  const { handleClickConversation } = useClickConversationCard();

  const { theme } = useTheme();
  const { activated, handleItemEnter, handleItemLeave } = useHandleItemHover();
  const {
    activated: conversationActivated,
    handleItemEnter: handleConversationItemEnter,
    handleItemLeave: handleConversationItemLeave,
  } = useHandleItemHover();
  const {
    conversationRenameLoading,
    initialConversationName,
    onConversationRenameOk,
    conversationRenameVisible,
    hideConversationRenameModal,
    showConversationRenameModal,
  } = useRenameConversation();
  const {
    dialogSettingLoading,
    initialDialog,
    onDialogEditOk,
    dialogEditVisible,
    clearDialog,
    hideDialogEditModal,
    showDialogEditModal,
  } = useEditDialog();
  const { t } = useTranslate('chat');
  const { currentRecord, setRecord } = useSetSelectedRecord<IDialog>();
  const [controller, setController] = useState(new AbortController());
  const { showEmbedModal, hideEmbedModal, embedVisible, beta } =
    useShowEmbedModal();

  const handleConversationCardEnter = (id: string) => () => {
    handleConversationItemEnter(id);
  };

  const handleRemoveConversation =
    (conversationId: string): MenuItemProps['onClick'] =>
    ({ domEvent }) => {
      domEvent.preventDefault();
      domEvent.stopPropagation();
      onRemoveConversation([conversationId]);
    };

  const handleShowConversationRenameModal =
    (conversationId: string): MenuItemProps['onClick'] =>
    ({ domEvent }) => {
      domEvent.preventDefault();
      domEvent.stopPropagation();
      showConversationRenameModal(conversationId);
    };

  const {
    list: conversationList,
    addTemporaryConversation,
    loading: conversationLoading,
  } = useSelectDerivedConversationList();
  const [conversationId, setConversationId] = useState('');

  const handleCreateTemporaryConversation = useCallback(() => {
    addTemporaryConversation();
    const searchParams = new URLSearchParams(window.location.search);
    const conversationId = searchParams.get('conversationId');
    // setConversationId(conversationId)
  }, [addTemporaryConversation]);

  useEffect(() => {
    // 反复横跳入口
    if (dialog.id !== dialogIdSearchParams) {
      handleClickDialog(dialog.id);
    }
  }, [dialog.id]);

  const [conversationListPage, setConversationListPage] =
    useState<any>(conversationList);

  useEffect(() => {
    if (conversationList.length > 0) {
      const firstDateItem = conversationList.find(
        (item) => item.type !== 'date',
      );
      setConversationId(firstDateItem ? conversationId : '');
    }
    //
    //
    //
    //
    setConversationListPage(conversationList);
  }, [conversationList]);

  const handleConversationCardClick = useCallback(
    (conversationId: string, isNew: boolean) => () => {
      setConversationId(conversationId);
    },
    [handleClickConversation],
  );

  const buildConversationItems = (conversationId: string) => {
    const appItems: MenuProps['items'] = [
      {
        key: '1',
        onClick: handleShowConversationRenameModal(conversationId),
        label: (
          <Space>
            <EditOutlined />
            重命名
          </Space>
        ),
      },
      { type: 'divider' },
      {
        key: '2',
        onClick: handleRemoveConversation(conversationId),
        label: (
          <Space>
            <DeleteOutlined />
            删除
          </Space>
        ),
      },
    ];

    return appItems;
  };

  return (
    <Flex className={styles.chatWrapper}>
      <Flex className={styles.chatTitleWrapper}>
        <Flex flex={1} vertical>
          <Flex
            justify={'space-between'}
            align="center"
            className={styles.chatTitle}
          >
            <Space>
              <Tooltip title={'新建会话'}>
                <div>
                  <Button
                    style={{
                      borderRadius: '26px',
                      background: '#1677ff',
                      color: '#FFF',
                      width: '165px',
                      fontSize: 16,
                      height: 35,
                    }}
                    name="plus-circle-fill"
                    width={30}
                    onClick={handleCreateTemporaryConversation}
                  >
                    {'新建会话'}
                  </Button>
                </div>
              </Tooltip>
            </Space>
          </Flex>
          <Divider></Divider>
          <Flex vertical gap={8} className={styles.chatTitleContent}>
            <Spin
              spinning={conversationLoading}
              wrapperClassName={styles.chatSpin}
            >
              {conversationListPage.map((x) => {
                if (x.type && x.type === 'date') {
                  return (
                    <span style={{ fontSize: 16, fontWeight: 500 }}>
                      {x.title}
                    </span>
                  );
                }
                return (
                  <Card
                    key={x.conversationId}
                    hoverable
                    onClick={handleConversationCardClick(
                      x.conversationId,
                      x.is_new,
                    )}
                    onMouseEnter={handleConversationCardEnter(x.conversationId)}
                    onMouseLeave={handleConversationItemLeave}
                    className={classNames(styles.chatTitleCard, {
                      [theme === 'dark'
                        ? styles.chatTitleCardSelectedDark
                        : styles.chatTitleCardSelected]:
                        x.conversationId === conversationId,
                    })}
                  >
                    <Flex justify="space-between" align="center">
                      <div>
                        <Text
                          ellipsis={{ tooltip: x.title }}
                          style={{
                            width: 150,
                            color:
                              x.conversationId === conversationId
                                ? '#0695ff'
                                : '#000',
                            fontWeight: 500,
                          }}
                        >
                          {x.title}
                        </Text>
                      </div>
                      {conversationActivated === x.conversationId &&
                        x.conversationId !== '' &&
                        !x.is_new && (
                          <section>
                            <Dropdown
                              menu={{
                                items: buildConversationItems(x.conversationId),
                              }}
                            >
                              <ChatAppCube
                                className={styles.cubeIcon}
                              ></ChatAppCube>
                            </Dropdown>
                          </section>
                        )}
                    </Flex>
                  </Card>
                );
              })}
            </Spin>
          </Flex>
        </Flex>
      </Flex>
      <Divider type={'vertical'} className={styles.divider}></Divider>
      <ChatContainer controller={controller} assistant={dialog}></ChatContainer>
      <RenameModal
        visible={conversationRenameVisible}
        hideModal={hideConversationRenameModal}
        onOk={onConversationRenameOk}
        initialName={initialConversationName}
        loading={conversationRenameLoading}
      ></RenameModal>

      {embedVisible && (
        <EmbedModal
          visible={embedVisible}
          hideModal={hideEmbedModal}
          token={currentRecord.id}
          form={SharedFrom.Chat}
          beta={beta}
          isAgent={false}
        ></EmbedModal>
      )}
    </Flex>
  );
};

export default Chat;
