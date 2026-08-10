package com.example.meeting.organization.infrastructure;

import java.time.LocalDateTime;
import java.util.List;
import java.util.Optional;
import org.apache.ibatis.annotations.Mapper;
import org.apache.ibatis.annotations.Param;
import org.apache.ibatis.annotations.Select;
import org.apache.ibatis.annotations.Update;

@Mapper
public interface EmployeeAdminMapper {

  String SELECT_COLUMNS =
      """
      u.id, u.username, u.display_name, u.email, u.department_id,
      d.name AS department_name, u.role, u.status, u.version,
      u.created_at, u.updated_at
      """;

  @Select(
      """
      SELECT id, name, default_building, default_floor, status
      FROM department
      WHERE status = 'ACTIVE'
      ORDER BY name, id
      """)
  List<com.example.meeting.organization.domain.Department> findActiveDepartments();

  @Select(
      """
      SELECT
      """
          + SELECT_COLUMNS
          + """
      FROM sys_user u
      LEFT JOIN department d ON d.id = u.department_id
      WHERE u.status = 'ACTIVE'
      ORDER BY d.name, u.display_name, u.id
      """)
  List<EmployeeAdminRow> findDirectoryEmployees();

  @Select(
      """
      SELECT
      """
          + SELECT_COLUMNS
          + """
      FROM sys_user u
      LEFT JOIN department d ON d.id = u.department_id
      WHERE u.id = #{id}
      """)
  Optional<EmployeeAdminRow> findById(@Param("id") long id);

  @Select(
      """
      <script>
      SELECT
      """
          + SELECT_COLUMNS
          + """
      FROM sys_user u
      LEFT JOIN department d ON d.id = u.department_id
      WHERE 1 = 1
      <if test="keyword != null">
        AND (LOWER(u.username) LIKE CONCAT('%', #{keyword}, '%')
          OR LOWER(u.display_name) LIKE CONCAT('%', #{keyword}, '%')
          OR LOWER(u.email) LIKE CONCAT('%', #{keyword}, '%'))
      </if>
      <if test="departmentId != null">AND u.department_id = #{departmentId}</if>
      <if test="role != null">AND u.role = #{role}</if>
      <if test="status != null">AND u.status = #{status}</if>
      ORDER BY u.created_at DESC, u.id DESC
      LIMIT #{limit} OFFSET #{offset}
      </script>
      """)
  List<EmployeeAdminRow> findEmployees(
      @Param("keyword") String keyword,
      @Param("departmentId") Long departmentId,
      @Param("role") String role,
      @Param("status") String status,
      @Param("limit") int limit,
      @Param("offset") long offset);

  @Select(
      """
      <script>
      SELECT COUNT(*) FROM sys_user u
      WHERE 1 = 1
      <if test="keyword != null">
        AND (LOWER(u.username) LIKE CONCAT('%', #{keyword}, '%')
          OR LOWER(u.display_name) LIKE CONCAT('%', #{keyword}, '%')
          OR LOWER(u.email) LIKE CONCAT('%', #{keyword}, '%'))
      </if>
      <if test="departmentId != null">AND u.department_id = #{departmentId}</if>
      <if test="role != null">AND u.role = #{role}</if>
      <if test="status != null">AND u.status = #{status}</if>
      </script>
      """)
  long countEmployees(
      @Param("keyword") String keyword,
      @Param("departmentId") Long departmentId,
      @Param("role") String role,
      @Param("status") String status);

  @Select("SELECT COUNT(*) FROM sys_user WHERE username = #{username}")
  int countByUsername(@Param("username") String username);

  @Select("SELECT COUNT(*) FROM sys_user WHERE email = #{email}")
  int countByEmail(@Param("email") String email);

  @Select("SELECT COUNT(*) FROM sys_user WHERE email = #{email} AND id != #{id}")
  int countOtherByEmail(@Param("email") String email, @Param("id") long id);

  @Update(
      """
      UPDATE sys_user
      SET display_name = #{displayName}, email = #{email}, department_id = #{departmentId},
          role = #{role}, updated_at = #{updatedAt}, version = version + 1
      WHERE id = #{id} AND version = #{expectedVersion}
      """)
  int updateEmployee(
      @Param("id") long id,
      @Param("displayName") String displayName,
      @Param("email") String email,
      @Param("departmentId") Long departmentId,
      @Param("role") String role,
      @Param("expectedVersion") int expectedVersion,
      @Param("updatedAt") LocalDateTime updatedAt);

  @Update(
      """
      UPDATE sys_user
      SET status = #{status}, updated_at = #{updatedAt}, version = version + 1
      WHERE id = #{id} AND version = #{expectedVersion}
      """)
  int updateStatus(
      @Param("id") long id,
      @Param("status") String status,
      @Param("expectedVersion") int expectedVersion,
      @Param("updatedAt") LocalDateTime updatedAt);

  @Update(
      """
      UPDATE sys_user
      SET password_hash = #{passwordHash}, updated_at = #{updatedAt}, version = version + 1
      WHERE id = #{id} AND version = #{expectedVersion}
      """)
  int updatePassword(
      @Param("id") long id,
      @Param("passwordHash") String passwordHash,
      @Param("expectedVersion") int expectedVersion,
      @Param("updatedAt") LocalDateTime updatedAt);
}
