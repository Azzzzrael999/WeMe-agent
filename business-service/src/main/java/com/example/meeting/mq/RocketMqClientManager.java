package com.example.meeting.mq;

import com.example.meeting.agentgateway.client.AgentBusinessResultCallback;
import com.example.meeting.agentgateway.client.AgentServiceProperties;
import com.example.meeting.outbox.MessageOutboxRecord;
import java.nio.charset.StandardCharsets;
import org.apache.rocketmq.client.consumer.DefaultMQPushConsumer;
import org.apache.rocketmq.client.consumer.listener.ConsumeConcurrentlyContext;
import org.apache.rocketmq.client.consumer.listener.ConsumeConcurrentlyStatus;
import org.apache.rocketmq.client.consumer.listener.MessageListenerConcurrently;
import org.apache.rocketmq.client.producer.DefaultMQProducer;
import org.apache.rocketmq.client.producer.SendResult;
import org.apache.rocketmq.client.producer.SendStatus;
import org.apache.rocketmq.common.consumer.ConsumeFromWhere;
import org.apache.rocketmq.common.message.Message;
import org.apache.rocketmq.common.message.MessageExt;
import org.springframework.context.SmartLifecycle;
import org.springframework.stereotype.Component;

@Component
public class RocketMqClientManager implements SmartLifecycle, MqMessagePublisher {

  private final RocketMqProperties properties;
  private final AgentServiceProperties agentServiceProperties;
  private final BookingCommandProcessor bookingCommandProcessor;
  private final AgentBusinessResultCallback resultCallback;

  private volatile boolean running;
  private DefaultMQProducer producer;
  private DefaultMQPushConsumer bookingConsumer;
  private DefaultMQPushConsumer resultConsumer;

  public RocketMqClientManager(
      RocketMqProperties properties,
      AgentServiceProperties agentServiceProperties,
      BookingCommandProcessor bookingCommandProcessor,
      AgentBusinessResultCallback resultCallback) {
    this.properties = properties;
    this.agentServiceProperties = agentServiceProperties;
    this.bookingCommandProcessor = bookingCommandProcessor;
    this.resultCallback = resultCallback;
  }

  @Override
  public synchronized void start() {
    if (running) {
      return;
    }
    if (!properties.enabled()) {
      running = true;
      return;
    }
    try {
      startProducer();
      bookingConsumer =
          startConsumer(
              properties.bookingConsumerGroup(),
              "BOOKING_COMMAND",
              body -> bookingCommandProcessor.process(body));
      if (agentServiceProperties.callbackEnabled()) {
        resultConsumer =
            startConsumer(
                properties.resultConsumerGroup(), "BOOKING_RESULT", resultCallback::deliver);
      }
      running = true;
    } catch (Exception exception) {
      stop();
      throw new IllegalStateException("RocketMQ client initialization failed", exception);
    }
  }

  private void startProducer() throws Exception {
    producer = new DefaultMQProducer("meeting-outbox-publisher");
    producer.setNamesrvAddr(properties.nameServer());
    producer.setSendMsgTimeout(3000);
    producer.start();
  }

  private DefaultMQPushConsumer startConsumer(
      String group, String tag, java.util.function.Consumer<String> handler) throws Exception {
    DefaultMQPushConsumer consumer = new DefaultMQPushConsumer(group);
    consumer.setNamesrvAddr(properties.nameServer());
    consumer.setConsumeFromWhere(ConsumeFromWhere.CONSUME_FROM_FIRST_OFFSET);
    consumer.subscribe(properties.bookingTopic(), tag);
    consumer.registerMessageListener(
        (MessageListenerConcurrently)
            (java.util.List<MessageExt> messages, ConsumeConcurrentlyContext context) -> {
              try {
                for (MessageExt message : messages) {
                  handler.accept(new String(message.getBody(), StandardCharsets.UTF_8));
                }
                return ConsumeConcurrentlyStatus.CONSUME_SUCCESS;
              } catch (RuntimeException exception) {
                return ConsumeConcurrentlyStatus.RECONSUME_LATER;
              }
            });
    consumer.start();
    return consumer;
  }

  @Override
  public void publish(MessageOutboxRecord record) {
    if (!properties.enabled() || !running || producer == null) {
      throw new IllegalStateException("RocketMQ producer is not ready");
    }
    try {
      Message message =
          new Message(
              record.getTopic(),
              record.getTag(),
              record.getEventId(),
              record.getPayloadJson().getBytes(StandardCharsets.UTF_8));
      SendResult result = producer.send(message, 3000);
      if (result == null || result.getSendStatus() != SendStatus.SEND_OK) {
        throw new IllegalStateException("RocketMQ did not acknowledge the message");
      }
    } catch (Exception exception) {
      throw new IllegalStateException("RocketMQ publish failed", exception);
    }
  }

  @Override
  public synchronized void stop() {
    if (resultConsumer != null) {
      resultConsumer.shutdown();
      resultConsumer = null;
    }
    if (bookingConsumer != null) {
      bookingConsumer.shutdown();
      bookingConsumer = null;
    }
    if (producer != null) {
      producer.shutdown();
      producer = null;
    }
    running = false;
  }

  @Override
  public boolean isRunning() {
    return running;
  }

  public boolean isReady() {
    return !properties.enabled() || (running && producer != null && bookingConsumer != null);
  }

  public boolean isDisabled() {
    return !properties.enabled();
  }
}
