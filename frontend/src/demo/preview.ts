export const lifecyclePreview = {
  preparation: [
    { title: '参会人员', status: 'CONFIRMED', detail: '6 位必需参会者已确认' },
    { title: '房间与设备', status: 'CONFIRMED', detail: '研发楼 302 · 大屏、白板' },
    { title: '会议议程', status: 'CONFIRMED', detail: '背景、方案、风险与决策四个环节已排期' },
    { title: '评审材料', status: 'WAITING_USER_INPUT', detail: '接口时序图尚未上传' },
    { title: '政策检查', status: 'SUCCESS', detail: '满足架构评审规范' },
  ],
  actions: [
    { title: '补充降级链路说明', owner: '张三', due: '周五 18:00', dependency: '等待接口时序图', type: '行动项草案', status: 'PENDING' },
    { title: '确认容量评估数字', owner: '王经理', due: '下周一', dependency: '容量基线报告', type: '待确认决策', status: 'WAITING_CONFIRMATION' },
  ],
  decision: '主链路采用分级限流方案，具体阈值在容量报告完成后确认。',
}
