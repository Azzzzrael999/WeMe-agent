package com.example.meeting.auth.infrastructure;

import com.baomidou.mybatisplus.core.mapper.BaseMapper;
import com.example.meeting.agentgateway.internal.ResolvedEmployeeRow;
import com.example.meeting.auth.domain.UserAccount;
import java.util.List;
import java.util.Optional;
import org.apache.ibatis.annotations.Mapper;
import org.apache.ibatis.annotations.Param;
import org.apache.ibatis.annotations.Select;

@Mapper
public interface UserMapper extends BaseMapper<UserAccount> {

  @Select(
      """
            SELECT id, username, password_hash, display_name, email,
                   department_id, role, status
            FROM sys_user
            WHERE username = #{username}
            LIMIT 1
            """)
  Optional<UserAccount> findByUsername(@Param("username") String username);

  @Select(
      """
            SELECT u.id, u.username, u.display_name, u.email, u.department_id,
                   d.name AS department_name, u.role, u.status
            FROM sys_user u
            LEFT JOIN department d ON d.id = u.department_id
            WHERE u.id = #{id}
            LIMIT 1
            """)
  Optional<UserProfileRow> findProfileById(@Param("id") long id);

  @Select(
      """
      <script>
      SELECT u.id AS employee_id, u.username, u.display_name, u.department_id,
             d.name AS department_name, u.status
      FROM sys_user u
      LEFT JOIN department d ON d.id = u.department_id
      WHERE u.status = 'ACTIVE'
        AND (
          <if test="names != null and !names.isEmpty()">
            (u.display_name IN
             <foreach collection="names" item="name" open="(" separator="," close=")">
               #{name}
             </foreach>
             OR u.username IN
             <foreach collection="names" item="name" open="(" separator="," close=")">
               #{name}
             </foreach>)
          </if>
          <if test="names != null and !names.isEmpty() and departmentNames != null and !departmentNames.isEmpty()">
            OR
          </if>
          <if test="departmentNames != null and !departmentNames.isEmpty()">
            d.name IN
            <foreach collection="departmentNames" item="departmentName" open="(" separator="," close=")">
              #{departmentName}
            </foreach>
          </if>
        )
      ORDER BY u.display_name, u.id
      LIMIT #{limit}
      </script>
      """)
  List<ResolvedEmployeeRow> resolveEmployees(
      @Param("names") List<String> names,
      @Param("departmentNames") List<String> departmentNames,
      @Param("limit") int limit);

  @Select(
      """
      <script>
      SELECT id
      FROM sys_user
      WHERE status = 'ACTIVE' AND id IN
      <foreach collection="ids" item="id" open="(" separator="," close=")">
        #{id}
      </foreach>
      </script>
      """)
  List<Long> findActiveIds(@Param("ids") List<Long> ids);
}
