import IndentedTree from './indented-tree';

import { useFetchKnowledgeGraph } from '@/hooks/knowledge-hooks';
import { IModalProps } from '@/interfaces/common';
import { Modal } from 'antd';

const IndentedTreeModal = ({
  visible,
  hideModal,
}: IModalProps<any> & { documentId: string }) => {
  const { data } = useFetchKnowledgeGraph();

  return (
    <Modal
      title={'思维导图'}
      open={visible}
      onCancel={hideModal}
      width={'90vw'}
      footer={null}
    >
      <section>
        <IndentedTree data={data?.mind_map} show></IndentedTree>
      </section>
    </Modal>
  );
};

export default IndentedTreeModal;
