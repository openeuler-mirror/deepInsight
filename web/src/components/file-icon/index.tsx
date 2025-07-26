import { getExtension } from '@/utils/document-util';
import SvgIcon from '../svg-icon';

import { useFetchDocumentThumbnailsByIds } from '@/hooks/document-hooks';
import { useEffect } from 'react';
import styles from './index.less';

interface IProps {
  name: string;
  id: string;
  url: string;
}

function getStandardFavicon(url) {
  const a = document.createElement('a');
  // 自动解析出 protocol + hostname
  a.href = url;
  return `${a.protocol}//${a.hostname}/favicon.ico`;
}

const FileIcon = ({ name, id }: IProps) => {
  const fileExtension = getExtension(name);

  const { data: fileThumbnails, setDocumentIds } =
    useFetchDocumentThumbnailsByIds();
  const fileThumbnail = fileThumbnails[id];

  useEffect(() => {
    if (id) {
      setDocumentIds([id]);
    }
  }, [id, setDocumentIds]);

  if (url) {
    return (
      <img
        style={{ height: 24, width: 24 }}
        src={getStandardFavicon(url)}
      ></img>
    );
  }
  return fileThumbnail ? (
    <img src={fileThumbnail} className={styles.thumbnailImg}></img>
  ) : (
    <SvgIcon name={`file-icon/${fileExtension}`} width={24}></SvgIcon>
  );
};

export default FileIcon;
