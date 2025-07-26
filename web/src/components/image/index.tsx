import { api_host } from '@/utils/api';
import { Popover } from 'antd';
import classNames from 'classnames';
import styles from './index.less';

interface IImage {
  id: string;
  className: string;
  onClick?(): void;
}

const Image = ({ id, className, ...props }: IImage) => {
  return (
    <img
      {...props}
      src={`${api_host}/document/image/${id}`}
      alt=""
      className={classNames('max-w-[45vw] max-h-[40wh] block', className)}
    />
  );
};

export default Image;

export const ImageWithPopover = ({ id }: { id: string }) => {
  return (
    <Popover
      placement="left"
      content={<Image id={id} className={styles.imagePreview}></Image>}
    >
      <Image id={id} className={styles.image}></Image>
    </Popover>
  );
};
