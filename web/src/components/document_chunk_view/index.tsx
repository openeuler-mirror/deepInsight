import { FileTextOutlined } from '@ant-design/icons';
import { Flex, Modal, Tooltip } from 'antd';
import { useState } from 'react';
import styles from './index.less';

const DocumentChunkView = ({ documentChunks }: { documentChunks: any[] }) => {
  const [visible, setVisible] = useState(false);
  const [selectedContent, setSelectedContent] = useState('');
  const [selectedTitle, setSelectedTitle] = useState('');

  const handleIconClick = (content: string, title: string) => {
    setSelectedContent(content);
    setSelectedTitle(title);
    setVisible(true);
  };

  const handleClose = () => {
    setVisible(false);
  };

  return (
    <div className={styles.documentChunkView}>
      {/* <h3>相关文档片段</h3> */}
      <Flex className={styles.documentChunkList}>
        {documentChunks?.map((result, index) => (
          <Tooltip key={result?.chunk_id || index} title={result?.docnm_kwd}>
            <div
              className={styles.documentChunkItem}
              onClick={() =>
                handleIconClick(result?.content_with_weight, result?.docnm_kwd)
              }
            >
              <FileTextOutlined className={styles.documentChunkIcon} />
              <span>{index + 1}</span>
            </div>
          </Tooltip>
        ))}
      </Flex>
      <Modal
        title={selectedTitle}
        open={visible}
        onCancel={handleClose}
        footer={null}
        width="50%"
      >
        <div className={styles.documentChunkItemView}>{selectedContent}</div>
      </Modal>
    </div>
  );
};

export default DocumentChunkView;
