-- ============================================
-- smart-analyst 核心库（Meta）—— 5 张核心表
-- 裁剪说明：users/quota_usage/audit_log 后置（产品化再建）；
--           task_events/metric_dict 砍掉（日志体系 / RAG 知识库承担）
-- ============================================

CREATE DATABASE IF NOT EXISTS meta
  DEFAULT CHARACTER SET utf8mb4
  COLLATE utf8mb4_unicode_ci;

USE meta;

-- ------------------------------------------------------------
-- 1. conversations 会话（thread_id 是 LangGraph 检查点锚点）
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS conversations (
  id              BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  user_id         BIGINT UNSIGNED NOT NULL DEFAULT 0 COMMENT '所属用户；users 表后置，恢复多用户时补 FK',
  thread_id       VARCHAR(64)     NOT NULL COMMENT 'LangGraph thread_id（检查点锚点）',
  title           VARCHAR(128)    NULL     COMMENT '首问摘要',
  status          ENUM('active','archived') NOT NULL DEFAULT 'active',
  created_at      DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP,
  last_active_at  DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (id),
  UNIQUE KEY uk_thread_id (thread_id),
  KEY idx_user_last_active (user_id, last_active_at)
) ENGINE=InnoDB COMMENT='会话表';

-- ------------------------------------------------------------
-- 2. messages 消息历史（含工具消息，供审计与 replay 恢复）
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS messages (
  id              BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  conversation_id BIGINT UNSIGNED NOT NULL,
  role            ENUM('user','assistant','tool','system') NOT NULL COMMENT '与 LangGraph 消息角色对齐',
  msg_type        VARCHAR(32)     NOT NULL COMMENT 'text/plan/worker_result/report/error',
  content         MEDIUMTEXT      NOT NULL COMMENT '正文或工具输出',
  meta            JSON            NULL     COMMENT '工具参数、来源引用、token 用量',
  created_at      DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (id),
  KEY idx_conv_created (conversation_id, created_at),
  CONSTRAINT fk_messages_conversation FOREIGN KEY (conversation_id)
    REFERENCES conversations (id) ON DELETE CASCADE
) ENGINE=InnoDB COMMENT='消息历史表';

-- ------------------------------------------------------------
-- 3. tasks 任务记录（与 AnalystState 一一对应，双闸守卫字段）
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS tasks (
  id              BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  conversation_id BIGINT UNSIGNED NOT NULL,
  thread_id       VARCHAR(64)     NOT NULL COMMENT '冗余存储，便于按 thread 恢复',
  task_spec       JSON            NOT NULL COMMENT 'intents/metrics/time_range/dims/compare',
  status          ENUM('running','awaiting_input','done','failed','degraded')
                                  NOT NULL DEFAULT 'running' COMMENT 'degraded=降级出报告',
  iteration       INT             NOT NULL DEFAULT 0 COMMENT '反思轮数（守卫闸1）',
  max_iterations  INT             NOT NULL DEFAULT 3,
  steps_used      INT             NOT NULL DEFAULT 0 COMMENT '工具调用次数（守卫闸2）',
  max_steps       INT             NOT NULL DEFAULT 20,
  cost_cents      DECIMAL(10,2)   NOT NULL DEFAULT 0.00 COMMENT 'LLM 成本审计',
  error_summary   TEXT            NULL,
  started_at      DATETIME        NULL,
  finished_at     DATETIME        NULL,
  PRIMARY KEY (id),
  KEY idx_conv_status (conversation_id, status),
  KEY idx_thread (thread_id),
  CONSTRAINT fk_tasks_conversation FOREIGN KEY (conversation_id)
    REFERENCES conversations (id) ON DELETE CASCADE
) ENGINE=InnoDB COMMENT='任务表';

-- ------------------------------------------------------------
-- 4. evidence 证据/引用（数字信任核心，validator 校验落点）
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS evidence (
  id           BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  task_id      BIGINT UNSIGNED NOT NULL,
  source_type  ENUM('sql','rag','web') NOT NULL COMMENT '来源类型',
  source_ref   VARCHAR(128)    NOT NULL COMMENT 'sql: query_id#row_key; rag: chunk_id; web: url',
  metric_code  VARCHAR(64)     NULL     COMMENT '关联指标（可空）',
  content      JSON            NOT NULL COMMENT '报告引用的原始依据数据',
  confidence   DECIMAL(3,2)    NULL     COMMENT '置信度 0-1',
  created_at   DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (id),
  UNIQUE KEY uk_task_source (task_id, source_ref) COMMENT '防重复证据',
  CONSTRAINT fk_evidence_task FOREIGN KEY (task_id)
    REFERENCES tasks (id) ON DELETE CASCADE
) ENGINE=InnoDB COMMENT='证据表';

-- ------------------------------------------------------------
-- 5. reports 报告产物（人工确认状态与 interrupt 联动）
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS reports (
  id          BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  task_id     BIGINT UNSIGNED NOT NULL,
  version     INT             NOT NULL DEFAULT 1 COMMENT '人工确认后修改递增',
  format      ENUM('markdown','pdf','json') NOT NULL,
  content     MEDIUMTEXT      NULL     COMMENT 'Markdown 正文',
  file_path   VARCHAR(512)    NULL     COMMENT '本地文件路径',
  object_key  VARCHAR(512)    NULL     COMMENT 'OSS 对象 key',
  checksum    CHAR(64)        NULL     COMMENT 'sha256 完整性校验',
  status      ENUM('draft','confirmed','delivered') NOT NULL DEFAULT 'draft',
  created_at  DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (id),
  UNIQUE KEY uk_task_version (task_id, version),
  CONSTRAINT fk_reports_task FOREIGN KEY (task_id)
    REFERENCES tasks (id) ON DELETE CASCADE
) ENGINE=InnoDB COMMENT='报告产物表';
