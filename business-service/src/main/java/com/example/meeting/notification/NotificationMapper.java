package com.example.meeting.notification;

import com.baomidou.mybatisplus.core.mapper.BaseMapper;
import java.time.LocalDateTime;
import java.util.List;
import java.util.Optional;
import org.apache.ibatis.annotations.Mapper;
import org.apache.ibatis.annotations.Param;
import org.apache.ibatis.annotations.Select;
import org.apache.ibatis.annotations.Update;

@Mapper
public interface NotificationMapper extends BaseMapper<NotificationRecord> {

  @Select(
      """
      <script>
      SELECT id, user_id, type, title, content, related_meeting_id,
             related_replan_case_id, read_at, created_at
      FROM notification
      WHERE user_id = #{userId}
      <if test="unreadOnly">AND read_at IS NULL</if>
      <if test="type != null">AND type = #{type}</if>
      ORDER BY created_at DESC, id DESC
      LIMIT #{limit} OFFSET #{offset}
      </script>
      """)
  List<NotificationRecord> findPage(
      @Param("userId") long userId,
      @Param("unreadOnly") boolean unreadOnly,
      @Param("type") String type,
      @Param("limit") int limit,
      @Param("offset") long offset);

  @Select(
      """
      <script>
      SELECT COUNT(*) FROM notification
      WHERE user_id = #{userId}
      <if test="unreadOnly">AND read_at IS NULL</if>
      <if test="type != null">AND type = #{type}</if>
      </script>
      """)
  long countPage(
      @Param("userId") long userId,
      @Param("unreadOnly") boolean unreadOnly,
      @Param("type") String type);

  @Select("SELECT COUNT(*) FROM notification WHERE user_id = #{userId} AND read_at IS NULL")
  long countUnread(@Param("userId") long userId);

  @Select(
      """
      SELECT id, user_id, type, title, content, related_meeting_id,
             related_replan_case_id, read_at, created_at
      FROM notification
      WHERE id = #{id} AND user_id = #{userId}
      """)
  Optional<NotificationRecord> findOwned(@Param("id") long id, @Param("userId") long userId);

  @Update(
      """
      UPDATE notification SET read_at = #{readAt}
      WHERE id = #{id} AND user_id = #{userId} AND read_at IS NULL
      """)
  int markRead(
      @Param("id") long id, @Param("userId") long userId, @Param("readAt") LocalDateTime readAt);

  @Update(
      """
      UPDATE notification SET read_at = #{readAt}
      WHERE user_id = #{userId} AND read_at IS NULL
      """)
  int markAllRead(@Param("userId") long userId, @Param("readAt") LocalDateTime readAt);
}
