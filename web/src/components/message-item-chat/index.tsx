import { useTranslate } from '@/hooks/common-hooks';
import { useDownloadFile } from '@/hooks/file-manager-hooks';
import {
  DeepResearchMessagePart,
  IReference,
} from '@/interfaces/database/chat';
import MarkdownContent from '@/pages/chat/markdown-content';
import { RightOutlined, UpOutlined } from '@ant-design/icons';
import { Card } from 'antd';
import { memo, useState } from 'react';
import styles from './index.less';

const MessageItemChat = ({
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

  return (
    <Card style={{ border: 'none' }} bodyStyle={{ padding: 0, paddingTop: 20 }}>
      <div onClick={() => setExpanded(!expanded)}>
        {/*思考过程*/}
        <span className={styles.title}>
          {t('reflecting')}
          &nbsp;&nbsp;{expanded ? <UpOutlined /> : <RightOutlined />}
        </span>
      </div>

      {expanded && (
        <Card
          style={{ border: 'none', marginTop: 9 }}
          bodyStyle={{ padding: 0 }}
        >
          <MarkdownContent
            loading={false}
            content={`> ${content[0]?.content.replace(/\n/g, '\n> ')}`}
            reference={{} as IReference}
          ></MarkdownContent>
        </Card>
      )}
    </Card>
  );
};

export default memo(MessageItemChat);
