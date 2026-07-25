package com.example.meeting.organization.infrastructure;

import com.baomidou.mybatisplus.core.mapper.BaseMapper;
import com.example.meeting.organization.domain.Department;
import org.apache.ibatis.annotations.Mapper;

@Mapper
public interface DepartmentMapper extends BaseMapper<Department> {}
