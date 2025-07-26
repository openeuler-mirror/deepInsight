import { useShowEmbedModal } from '@/components/api-service/hooks';
import { useTheme } from '@/components/theme-provider';
import { SourcesType } from '@/constants/chat';
import { useClickDialogCard, useGetDialogId } from '@/hooks/chat-hooks';
import { useTranslate } from '@/hooks/common-hooks';
import {
  useDeleteDocument,
  useFetchDocumentInfosByIds,
  useRemoveNextDocument,
  useUploadAndParseDocument,
} from '@/hooks/document-hooks';
import { useSetSelectedRecord } from '@/hooks/logic-hooks';
import { IDialog } from '@/interfaces/database/chat';
import { cn } from '@/lib/utils';
import {
  useDeleteDialog,
  useEditDialog,
  useHandleItemHover,
} from '@/pages/chat/hooks';
import { getExtension } from '@/utils/document-util';
import { formatBytes } from '@/utils/file-util';
import {
  CheckOutlined,
  CloseCircleOutlined,
  EditOutlined,
  InfoCircleOutlined,
  LoadingOutlined,
} from '@ant-design/icons';
import {
  Avatar,
  Button,
  Card,
  Flex,
  GetProp,
  Input,
  List,
  MenuItemProps,
  MenuProps,
  Space,
  Spin,
  Typography,
  Upload,
  UploadFile,
  UploadProps,
} from 'antd';
import classNames from 'classnames';
import get from 'lodash/get';
import { CircleStop, Paperclip, SendHorizontal } from 'lucide-react';
import {
  ChangeEventHandler,
  memo,
  useCallback,
  useEffect,
  useRef,
  useState,
} from 'react';
import FileIcon from '../file-icon';
import styles from './index.less';

type FileType = Parameters<GetProp<UploadProps, 'beforeUpload'>>[0];
const { Text } = Typography;

const { TextArea } = Input;

const getFileId = (file: UploadFile) => get(file, 'response.data.0');

const getFileIds = (fileList: UploadFile[]) => {
  const ids = fileList.reduce((pre, cur) => {
    return pre.concat(get(cur, 'response.data', []));
  }, []);

  return ids;
};

const isUploadSuccess = (file: UploadFile) => {
  const code = get(file, 'response.code');
  return typeof code === 'number' && code === 0;
};

interface IProps {
  disabled: boolean;
  value: string;
  sendDisabled: boolean;
  sendLoading: boolean;

  onPressEnter(documentIds: string[]): void;

  onInputChange: ChangeEventHandler<HTMLTextAreaElement>;
  conversationId: string;
  uploadMethod?: string;
  isShared?: boolean;
  showUploadIcon?: boolean;

  createConversationBeforeUploadDocument?(message: string): Promise<any>;

  stopOutputMessage?(): void;

  isDeepinsight?: boolean;
  switchDeepinsight?: (enable: boolean) => Promise<void>;
  assistant: object;
  type: string;

  handlePlanButtonClick(str: string[]): void;
}

const getBase64 = (file: FileType): Promise<string> =>
  new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.readAsDataURL(file as any);
    reader.onload = () => resolve(reader.result as string);
    reader.onerror = (error) => reject(error);
  });

const MessageInput = ({
  isShared = false,
  disabled,
  value,
  onPressEnter,
  sendDisabled,
  sendLoading,
  onInputChange,
  conversationId,
  showUploadIcon = false,
  createConversationBeforeUploadDocument,
  uploadMethod = 'upload_and_parse',
  stopOutputMessage,
  isDeepinsight,
  switchDeepinsight,
  type,
}: IProps) => {
  const { t } = useTranslate('chat');
  const { removeDocument } = useRemoveNextDocument();
  const { deleteDocument } = useDeleteDocument();
  const { data: documentInfos, setDocumentIds } = useFetchDocumentInfosByIds();
  const { uploadAndParseDocument } = useUploadAndParseDocument(uploadMethod);
  const conversationIdRef = useRef(conversationId);
  const { dialogId } = useGetDialogId();
  const [fileList, setFileList] = useState<UploadFile[]>([]);
  const [buttonState, setbuttonState] = useState(false);
  const { theme } = useTheme();
  const { activated, handleItemEnter, handleItemLeave, handleItemClick } =
    useHandleItemHover();
  const {
    activated: conversationActivated,
    handleItemEnter: handleConversationItemEnter,
    handleItemLeave: handleConversationItemLeave,
  } = useHandleItemHover();
  const { handleClickDialog } = useClickDialogCard();
  const [normal, setNormal] = useState('普通');
  const [expert, setExpert] = useState('专家');
  const {
    dialogSettingLoading,
    initialDialog,
    onDialogEditOk,
    dialogEditVisible,
    clearDialog,
    hideDialogEditModal,
    showDialogEditModal,
  } = useEditDialog();
  const handleShowChatConfigurationModal =
    (dialogId?: string): any =>
    (info: any) => {
      info?.domEvent?.preventDefault();
      info?.domEvent?.stopPropagation();
      showDialogEditModal(dialogId);
    };

  const handlePreview = async (file: UploadFile) => {
    if (!file.url && !file.preview) {
      file.preview = await getBase64(file.originFileObj as FileType);
    }
  };

  const handleChange: UploadProps['onChange'] = async ({
    // fileList: newFileList,
    file,
  }) => {
    let nextConversationId: string = conversationId;
    if (createConversationBeforeUploadDocument) {
      const creatingRet = await createConversationBeforeUploadDocument(
        file.name,
      );
      if (creatingRet?.code === 0) {
        nextConversationId = creatingRet.data.id;
      }
    }
    setFileList((list) => {
      list.push({
        ...file,
        status: 'uploading',
        originFileObj: file as any,
      });
      return [...list];
    });
    const ret = await uploadAndParseDocument({
      conversationId: nextConversationId,
      fileList: [file],
    });
    setFileList((list) => {
      const nextList = list.filter((x) => x.uid !== file.uid);
      nextList.push({
        ...file,
        originFileObj: file as any,
        response: ret,
        percent: 100,
        status: ret?.code === 0 ? 'done' : 'error',
      });
      return nextList;
    });
  };

  // console.log('assistant========', assistant)

  const isUploadingFile = fileList.some((x) => x.status === 'uploading');

  const handlePressEnter = useCallback(async () => {
    // console.log('发送请求：-------')
    if (isUploadingFile) return;
    const ids = getFileIds(fileList.filter((x) => isUploadSuccess(x)));

    onPressEnter(ids);
    setFileList([]);
  }, [fileList, onPressEnter, isUploadingFile]);

  const handleKeyDown = useCallback(
    async (event: React.KeyboardEvent<HTMLTextAreaElement>) => {
      // check if it was shift + enter
      if (event.key === 'Enter' && event.shiftKey) return;
      if (event.key !== 'Enter') return;
      if (sendDisabled || isUploadingFile || sendLoading) return;

      event.preventDefault();
      handlePressEnter();
    },
    [sendDisabled, isUploadingFile, sendLoading, handlePressEnter],
  );

  const handleRemove = useCallback(
    async (file: UploadFile) => {
      const ids = get(file, 'response.data', []);
      // Upload Successfully
      if (Array.isArray(ids) && ids.length) {
        if (isShared) {
          await deleteDocument(ids);
        } else {
          await removeDocument(ids[0]);
        }
        setFileList((preList) => {
          return preList.filter((x) => getFileId(x) !== ids[0]);
        });
      } else {
        // Upload failed
        setFileList((preList) => {
          return preList.filter((x) => x.uid !== file.uid);
        });
      }
    },
    [removeDocument, deleteDocument, isShared],
  );

  const handleStopOutputMessage = useCallback(() => {
    stopOutputMessage?.();
  }, [stopOutputMessage]);

  const getDocumentInfoById = useCallback(
    (id: string) => {
      return documentInfos.find((x) => x.id === id);
    },
    [documentInfos],
  );

  const handleAppCardEnter = (id: string) => () => {
    handleItemEnter(id);
  };
  const { onRemoveDialog } = useDeleteDialog();
  const handleConversationCardEnter = (id: string) => () => {
    handleConversationItemEnter(id);
  };

  // 在组件顶部添加状态管理
  const [webEnabled, setWebEnabled] = useState(
    localStorage.getItem(getKeyName(SourcesType.web_search)) === 'true',
  );
  const [knowledgeEnabled, setKnowledgeEnabled] = useState(
    localStorage.getItem(getKeyName(SourcesType.knowledge)) === 'true',
  );
  const [intraEnabled, setIntraEnabled] = useState(
    localStorage.getItem(getKeyName(SourcesType.intra_search)) === 'true',
  );
  const [expertEnabled, setExpertEnabled] = useState(
    localStorage.getItem(getKeyName(SourcesType.deepinsight_mode)) === 'true',
  );

  function getKeyName(key: string) {
    return 'sources_' + key;
  }

  const handleDialogCardClick = (type: string) => () => {
    if (type === getKeyName(SourcesType.knowledge)) {
      const newValue = !knowledgeEnabled;
      localStorage.setItem(getKeyName(SourcesType.knowledge), String(newValue));
      setKnowledgeEnabled(newValue);
    }

    if (type === getKeyName(SourcesType.intra_search)) {
      const newValue = !intraEnabled;
      localStorage.setItem(
        getKeyName(SourcesType.intra_search),
        String(newValue),
      );
      setIntraEnabled(newValue);
    }

    if (type === getKeyName(SourcesType.web_search)) {
      const newValue = !webEnabled;
      localStorage.setItem(
        getKeyName(SourcesType.web_search),
        String(newValue),
      );
      setWebEnabled(newValue);
    }

    if (type === getKeyName(SourcesType.deepinsight_mode)) {
      const newValue = !expertEnabled;
      localStorage.setItem(
        getKeyName(SourcesType.deepinsight_mode),
        String(newValue),
      );
      setExpertEnabled(newValue);
    }
  };

  const { showEmbedModal, hideEmbedModal, embedVisible, beta } =
    useShowEmbedModal();
  const { currentRecord, setRecord } = useSetSelectedRecord<IDialog>();
  const buildAppItems = (dialog: IDialog) => {
    if (!dialog) {
      return;
    }

    const dialogId = dialog.id;

    const handleRemoveDialog =
      (dialogId: string): MenuItemProps['onClick'] =>
      ({ domEvent }) => {
        domEvent.preventDefault();
        domEvent.stopPropagation();
        onRemoveDialog([dialogId]);
      };

    const handleShowOverviewModal =
      (dialog: IDialog): any =>
      (info: any) => {
        info?.domEvent?.preventDefault();
        info?.domEvent?.stopPropagation();
        setRecord(dialog);
        showEmbedModal();
      };
    const appItems: MenuProps['items'] = [
      {
        key: '1',
        onClick: handleShowChatConfigurationModal(dialogId),
        label: (
          <Space>
            <EditOutlined />
            {t('edit', { keyPrefix: 'common' })}
          </Space>
        ),
      },
    ];

    return appItems;
  };

  useEffect(() => {
    const ids = getFileIds(fileList);
    setDocumentIds(ids);
  }, [fileList, setDocumentIds]);

  useEffect(() => {
    if (
      conversationIdRef.current &&
      conversationId !== conversationIdRef.current
    ) {
      setFileList([]);
    }
    conversationIdRef.current = conversationId;
    // 第一次进来
    // 默认打开知识库
    if (type === 'chat') {
      localStorage.setItem(getKeyName(SourcesType.knowledge), 'true');
      localStorage.setItem(getKeyName(SourcesType.intra_search), 'false');
      localStorage.setItem(getKeyName(SourcesType.web_search), 'false');
      localStorage.setItem(getKeyName(SourcesType.deepinsight_mode), 'false');
      setKnowledgeEnabled(true);
      setIntraEnabled(false);
      setWebEnabled(false);
      setExpert('增强');
      setExpertEnabled(false);
    } else {
      localStorage.setItem(getKeyName(SourcesType.knowledge), 'false');
      localStorage.setItem(getKeyName(SourcesType.intra_search), 'false');
      localStorage.setItem(getKeyName(SourcesType.web_search), 'true');
      localStorage.setItem(getKeyName(SourcesType.deepinsight_mode), 'false');
      setWebEnabled(true);
      setKnowledgeEnabled(false);
      setIntraEnabled(false);
      setExpert('专家');
      setExpertEnabled(false);
    }

    const handleClickOutside = (e) => {
      if (dropdownRef.current && !dropdownRef.current.contains(e.target)) {
        setVisible(false); // 点击外部时关闭
      }
    };
    document.addEventListener('click', handleClickOutside);
    return () => document.removeEventListener('click', handleClickOutside);
  }, [conversationId, setFileList]);

  const items: MenuProps['items'] = [
    {
      key: '1',
      label: (
        <Button
          style={{ width: '100%', color: '#000', justifyContent: 'flex-start' }}
          type="link"
        >
          内网{intraEnabled ? <CheckOutlined /> : null}
        </Button>
      ),
    },
    {
      key: '2',
      label: (
        <Button
          style={{ width: '100%', color: '#000', justifyContent: 'flex-start' }}
          type="link"
        >
          外网{webEnabled ? <CheckOutlined /> : null}
        </Button>
      ),
    },
  ];

  const handleMenuClick: MenuProps['onClick'] = (e) => {
    e.domEvent.stopPropagation();
    if (e.key === '1') {
      const newValue = !intraEnabled;
      localStorage.setItem(
        getKeyName(SourcesType.intra_search),
        String(newValue),
      );
      setIntraEnabled(newValue);
    } else if (e.key === '2') {
      const newValue = !webEnabled;
      localStorage.setItem(
        getKeyName(SourcesType.web_search),
        String(newValue),
      );
      setWebEnabled(newValue);
    }
  };
  const [visible, setVisible] = useState(false);
  const dropdownRef = useRef(null);
  return (
    <Flex
      gap={1}
      style={{
        borderRadius: '20px',
        backgroundColor: '#ffffffff',
        width: type === 'chat' ? '70%' : '',
      }}
      vertical
      className={cn(styles.messageInputWrapper, '')}
    >
      <TextArea
        size="large"
        placeholder={'给助理发送消息...'}
        value={value}
        allowClear
        disabled={disabled}
        style={{
          border: 'none',
          boxShadow: 'none',
          padding: '10px 10px',
          marginTop: 10,
          borderRadius: '40px',
          marginBottom: '10px',
        }}
        autoSize={{ minRows: 2, maxRows: 3 }}
        onKeyDown={handleKeyDown}
        onChange={onInputChange}
      />
      <Flex justify="flex-start" align="flex-start">
        <Button
          className={
            webEnabled || intraEnabled
              ? styles.activeButton
              : styles.defaultButton
          }
          ref={dropdownRef}
        >
          <Avatar
            src="/icon-internet - black.svg"
            shape={'square'}
            size={18}
            style={{
              filter:
                webEnabled || intraEnabled
                  ? 'invert(0.5) sepia(100) saturate(50) hue-rotate(200deg)'
                  : 'none',
            }}
          />{' '}
          搜索模式
        </Button>

        {fileList.length > 0 && (
          <List
            grid={{
              gutter: 16,
              xs: 1,
              sm: 1,
              md: 1,
              lg: 1,
              xl: 2,
              xxl: 4,
            }}
            dataSource={fileList}
            className={styles.listWrapper}
            renderItem={(item) => {
              const id = getFileId(item);
              const documentInfo = getDocumentInfoById(id);
              const fileExtension = getExtension(documentInfo?.name ?? '');
              const fileName = item.originFileObj?.name ?? '';

              return (
                <List.Item>
                  <Card className={styles.documentCard}>
                    <Flex gap={10} align="center">
                      {item.status === 'uploading' ? (
                        <Spin
                          indicator={
                            <LoadingOutlined style={{ fontSize: 24 }} spin />
                          }
                        />
                      ) : item.status === 'error' ? (
                        <InfoCircleOutlined size={30}></InfoCircleOutlined>
                      ) : (
                        <FileIcon id={id} name={fileName}></FileIcon>
                      )}
                      <Flex vertical style={{ width: '90%' }}>
                        <Text
                          ellipsis={{ tooltip: fileName }}
                          className={styles.nameText}
                        >
                          <b> {fileName}</b>
                        </Text>
                        {item.status === 'error' ? (
                          t('uploadFailed')
                        ) : (
                          <>
                            {item.percent !== 100 ? (
                              t('uploading')
                            ) : !item.response ? (
                              t('parsing')
                            ) : (
                              <Space>
                                <span>{fileExtension?.toUpperCase()},</span>
                                <span>
                                  {formatBytes(
                                    getDocumentInfoById(id)?.size ?? 0,
                                  )}
                                </span>
                              </Space>
                            )}
                          </>
                        )}
                      </Flex>
                    </Flex>

                    {item.status !== 'uploading' && (
                      <span className={styles.deleteIcon}>
                        <CloseCircleOutlined
                          onClick={() => handleRemove(item)}
                        />
                      </span>
                    )}
                  </Card>
                </List.Item>
              );
            }}
          />
        )}
        <Flex
          gap={5}
          align="center"
          justify="space-between"
          style={{
            paddingRight: 10,
            paddingBottom: 10,
            width: fileList.length > 0 ? '50%' : '100%',
          }}
        >
          {/* current version is hidden this funciton */}
          {false ? (
            <Button
              type={isDeepinsight ? 'primary' : 'text'}
              className={classNames(styles.deepinsightIcon, [
                { isDeepinsight: styles.disable },
              ])}
              onClick={() => switchDeepinsight?.(!isDeepinsight)}
            >
              {t('deepinsight')}
            </Button>
          ) : (
            <div></div>
          )}
          <Flex>
            {showUploadIcon && (
              <Upload
                onPreview={handlePreview}
                onChange={handleChange}
                multiple={false}
                onRemove={handleRemove}
                showUploadList={false}
                beforeUpload={() => {
                  return false;
                }}
              >
                <Button type={'primary'} disabled={disabled}>
                  <Paperclip className="size-4" />
                </Button>
              </Upload>
            )}
            {sendLoading ? (
              <Button
                onClick={handleStopOutputMessage}
                style={{
                  borderRadius: '50%',
                  height: '50px',
                  width: '50px',
                }}
              >
                <CircleStop className="size-5" />
              </Button>
            ) : (
              <Button
                type="primary"
                onClick={handlePressEnter}
                loading={sendLoading}
                disabled={
                  sendDisabled ||
                  isUploadingFile ||
                  sendLoading ||
                  !(webEnabled || intraEnabled || knowledgeEnabled)
                }
                style={{
                  borderRadius: '50%',
                  height: '50px',
                  width: '50px',
                }}
              >
                <SendHorizontal className="size-5" />
              </Button>
            )}
          </Flex>
        </Flex>
      </Flex>
    </Flex>
  );
};

export default memo(MessageInput);
