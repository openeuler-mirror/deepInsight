#!/usr/bin/env python3
"""
从 config.yaml 读取模型配置并下载到本地
"""
import os
import sys
import yaml

def download_models():
    # 读取配置文件 (在 Docker 中,config.yaml 在 /deepinsight/ 目录)
    config_path = '/deepinsight/config.yaml'
    
    # 如果不在 Docker 环境,尝试相对路径
    if not os.path.exists(config_path):
        config_path = os.path.join(os.path.dirname(__file__), '..', 'config.yaml')
    
    if not os.path.exists(config_path):
        print(f"错误: 找不到配置文件 {config_path}")
        sys.exit(1)
    
    print(f"读取配置文件: {config_path}")
    
    with open(config_path, 'r', encoding='utf-8') as f:
        config = yaml.safe_load(f)
    
    # 获取模型配置
    rag_config = config.get('rag', {})
    engine_config = rag_config.get('engine', {})
    engine_type = engine_config.get('type', 'llamaindex')
    
    # 设置缓存目录
    cache_dir = os.environ.get('SENTENCE_TRANSFORMERS_HOME', '/deepinsight/models')
    os.makedirs(cache_dir, exist_ok=True)
    
    print(f"RAG 引擎类型: {engine_type}")
    print(f"模型缓存目录: {cache_dir}")
    
    # 根据引擎类型下载对应的模型
    if engine_type == 'llamaindex':
        llamaindex_config = engine_config.get('llamaindex', {})
        embed_model = llamaindex_config.get('embed_model', 'BAAI/bge-small-en-v1.5')
        
        print(f"\n正在下载 LlamaIndex 嵌入模型: {embed_model}")
        
        try:
            from sentence_transformers import SentenceTransformer
            model = SentenceTransformer(embed_model, cache_folder=cache_dir)
            print(f"✅ 模型下载成功: {embed_model}")
        except Exception as e:
            print(f"❌ 模型下载失败: {e}")
            sys.exit(1)
            
    elif engine_type == 'lightrag':
        lightrag_config = engine_config.get('lightrag', {})
        embed_model = lightrag_config.get('embedding_model', 'sentence-transformers/all-MiniLM-L6-v2')
        
        print(f"\n正在下载 LightRAG 嵌入模型: {embed_model}")
        
        try:
            from sentence_transformers import SentenceTransformer
            model = SentenceTransformer(embed_model, cache_folder=cache_dir)
            print(f"✅ 模型下载成功: {embed_model}")
        except Exception as e:
            print(f"❌ 模型下载失败: {e}")
            sys.exit(1)
    else:
        print(f"⚠️  未知的引擎类型: {engine_type}")
    
    print(f"\n所有模型已下载到: {cache_dir}")

if __name__ == '__main__':
    download_models()
