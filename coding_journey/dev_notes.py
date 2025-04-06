import abc


class MyAbstractClass(abc.ABC):
    
    @abc.abstractmethod
    def abstractmethod(self) -> None:
        pass
