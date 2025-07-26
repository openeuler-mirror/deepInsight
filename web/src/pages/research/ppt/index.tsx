import { init } from 'pptx-preview';
import { useEffect } from 'react';

interface IProps {
  url: string;
  type: string;
}

const PptPreviewer = ({ url, type }: IProps) => {
  let width = window.innerWidth;
  let height = window.innerHeight;

  if (type === 'small') {
    width = width * 0.48;
    height = height * 0.9;
  }

  useEffect(() => {
    let pptxPrviewer = init(document.getElementById('ppt-wrapper'), {
      width: width,
      height: height,
    });
    // 获取 PPTX 文件的 ArrayBuffer 数据
    fetch(url)
      .then((response) => response.arrayBuffer())
      .then((res) => {
        pptxPrviewer.preview(res); // 预览文件
      })
      .catch((error) => {
        console.error('加载 PPTX 文件失败:', error);
      });

    // 组件卸载时清除预览实例
    return () => {
      pptxPrviewer = null;
    };
  }, []);

  return (
    <div style={{ width: '100%', height: '100%' }} id={'ppt-wrapper'}></div>
  );
};

export default PptPreviewer;
